package app.commonplace.engine

import android.content.Context
import com.google.ai.edge.litertlm.Backend as LiteBackend
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.SamplerConfig
import uniffi.commonplace_ffi.CpException
import uniffi.commonplace_ffi.ForeignLlm
import uniffi.commonplace_ffi.GenStatsRecord
import uniffi.commonplace_ffi.TokenSink
import java.io.File
import java.util.concurrent.CountDownLatch

/**
 * LiteRT-LM on the Tensor TPU (gate G0, PLAN.md §17). Tries the NPU first and falls back to
 * the CPU. The Rust orchestrator calls [generate] on its own worker thread and blocks on it.
 * LiteRT-LM has no GBNF; for JSON calls the orchestrator parses the output tolerantly.
 */
class LiteRtBackend(private val context: Context, private val modelPath: String) : ForeignLlm {
    private var engine: Engine? = null
    private val lock = Any() // one generate at a time; close() waits for it so the engine never closes under a conversation
    @Volatile private var running: Conversation? = null
    private var backendName = "npu"

    fun initialize() {
        if (engine != null) return
        val cache = context.cacheDir.path
        // Only files compiled for the Tensor NPU go to the NPU; generic files run on LiteRT's CPU backend.
        if (!File(modelPath).name.contains("_Google_Tensor_")) {
            backendName = "cpu"
            engine = Engine(EngineConfig(modelPath = modelPath, backend = LiteBackend.CPU(), cacheDir = cache)).also { it.initialize() }
            return
        }
        engine = try {
            Engine(EngineConfig(modelPath = modelPath, backend = LiteBackend.NPU(nativeLibraryDir = context.applicationInfo.nativeLibraryDir), cacheDir = cache))
                .also { it.initialize() }
        } catch (t: Throwable) {
            // Gate G0 evidence: why the Tensor NPU is unavailable on this device.
            android.util.Log.w("Commonplace", "LiteRT-LM NPU unavailable, falling back to CPU", t)
            backendName = "cpu"
            Engine(EngineConfig(modelPath = modelPath, backend = LiteBackend.CPU(), cacheDir = cache)).also { it.initialize() }
        }
    }

    fun close() {
        running?.cancelProcess()
        synchronized(lock) {
            engine?.close()
            engine = null
        }
    }

    override fun id(): String = "${File(modelPath).name} (LiteRT-LM, $backendName)"

    override fun generate(
        system: String,
        user: String,
        maxTokens: UInt,
        temperature: Float,
        topP: Float,
        jsonOnly: Boolean,
        sink: TokenSink,
    ): GenStatsRecord = synchronized(lock) {
        val e = engine ?: throw CpException.Failed("LiteRT-LM engine is not initialized")
        val cfg = ConversationConfig(
            systemInstruction = Contents.of(system),
            samplerConfig = SamplerConfig(topK = 40, topP = topP.toDouble(), temperature = temperature.toDouble().coerceAtLeast(0.01)),
        )
        val start = System.nanoTime()
        var first = 0L
        var tokens = 0u
        var error: Throwable? = null
        val done = CountDownLatch(1)
        e.createConversation(cfg).use { conv ->
            running = conv
            conv.sendMessageAsync(
                user,
                object : MessageCallback {
                    override fun onMessage(message: Message) {
                        if (first == 0L) first = System.nanoTime()
                        tokens++
                        if (!sink.onToken(message.toString()) || tokens >= maxTokens) conv.cancelProcess()
                    }

                    override fun onDone() = done.countDown()

                    override fun onError(throwable: Throwable) {
                        error = throwable
                        done.countDown()
                    }
                },
            )
            done.await()
            running = null
        }
        error?.let { if (it !is java.util.concurrent.CancellationException) throw CpException.Failed(it.message ?: it.toString()) }
        val end = System.nanoTime()
        val firstAt = if (first == 0L) end else first
        GenStatsRecord(
            // LiteRT-LM does not report the prompt token count; estimate from characters.
            promptTokens = ((system.length + user.length) / 4).toUInt(),
            genTokens = tokens,
            prefillMs = (firstAt - start) / 1e6,
            decodeMs = (end - firstAt) / 1e6,
        )
    }
}
