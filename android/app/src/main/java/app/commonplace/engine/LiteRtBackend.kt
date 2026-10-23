package app.commonplace.engine

import android.content.Context
import com.google.ai.edge.litertlm.Backend as LiteBackend
import com.google.ai.edge.litertlm.BenchmarkInfo
import com.google.ai.edge.litertlm.Channel
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.ExperimentalApi
import com.google.ai.edge.litertlm.ExperimentalFlags
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ThinkingConfig
import uniffi.commonplace_ffi.CpException
import uniffi.commonplace_ffi.ForeignLlm
import uniffi.commonplace_ffi.GenStatsRecord
import uniffi.commonplace_ffi.TokenSink
import java.io.File
import java.util.concurrent.CountDownLatch

/**
 * LiteRT-LM for Gemma packs. The file name picks the accelerator: a file compiled for the Tensor NPU
 * (`_Google_Tensor_`) runs on the NPU, a `-gpu` file on the GPU, any other file on LiteRT's CPU backend.
 * [initialize] throws when the accelerator is unavailable; the caller then uses llama.cpp.
 * The Rust orchestrator calls [generate] on its own worker thread and blocks on it.
 * LiteRT-LM has no GBNF; for JSON calls the orchestrator parses the output tolerantly.
 */
@OptIn(ExperimentalApi::class)
class LiteRtBackend(private val context: Context, private val modelPath: String, private val speculative: Boolean) : ForeignLlm {
    private var engine: Engine? = null
    private val lock = Any() // one generate at a time; close() waits for it so the engine never closes under a conversation
    @Volatile private var running: Conversation? = null
    private val name = File(modelPath).name
    private val backendName = when {
        name.contains("_Google_Tensor_") -> "npu"
        name.contains("-gpu") -> "gpu"
        else -> "cpu"
    }

    fun initialize() {
        if (engine != null) return
        // Process-wide flags, read when the engine is created. The benchmark gives the real token counts.
        ExperimentalFlags.enableBenchmark = true
        // The NPU files carry the draft model: decode 19 → 27–31 tok/s on a Pixel 10 (docs/BENCHMARKS.md). GPU and CPU are unmeasured.
        ExperimentalFlags.enableSpeculativeDecoding = speculative && backendName == "npu"
        val backend = when (backendName) {
            "npu" -> LiteBackend.NPU(nativeLibraryDir = context.applicationInfo.nativeLibraryDir)
            "gpu" -> LiteBackend.GPU()
            else -> LiteBackend.CPU()
        }
        engine = Engine(EngineConfig(modelPath = modelPath, backend = backend, cacheDir = context.cacheDir.path)).also { it.initialize() }
    }

    fun close() {
        running?.cancelProcess()
        synchronized(lock) {
            engine?.close()
            engine = null
        }
    }

    override fun id(): String = "$name (LiteRT-LM, $backendName${if (backendName == "npu" && speculative) ", speculative" else ""})"

    override fun generate(
        system: String,
        user: String,
        maxTokens: UInt,
        temperature: Float,
        topP: Float,
        jsonOnly: Boolean,
        thinkBudget: UInt?,
        sink: TokenSink,
    ): GenStatsRecord = synchronized(lock) {
        val e = engine ?: throw CpException.Failed("LiteRT-LM engine is not initialized")
        val cfg = ConversationConfig(
            systemInstruction = Contents.of(system),
            samplerConfig = SamplerConfig(topK = 40, topP = topP.toDouble(), temperature = temperature.toDouble().coerceAtLeast(0.01)),
            // Gemma 4 writes its reasoning as <|channel>thought … <channel|>; LiteRT moves it into Message.channels.
            channels = listOf(Channel("thought", "<|channel>thought", "<channel|>")),
        )
        val start = System.nanoTime()
        var first = 0L
        var tokens = 0u
        var error: Throwable? = null
        var bench: BenchmarkInfo? = null
        var thinking = false
        // Reasoning tokens do not count against the answer's limit, as with llama.cpp.
        val limit = maxTokens + (thinkBudget ?: 0u)
        val done = CountDownLatch(1)
        e.createConversation(cfg).use { conv ->
            running = conv
            conv.sendMessageAsync(
                user,
                object : MessageCallback {
                    override fun onMessage(message: Message) {
                        if (first == 0L) first = System.nanoTime()
                        tokens++
                        // The reasoning arrives in a channel; the engine reads it between <think> and </think>.
                        val thought = message.channels.values.joinToString("")
                        val text = message.toString()
                        val out = buildString {
                            if (thought.isNotEmpty()) {
                                if (!thinking) append("<think>")
                                thinking = true
                                append(thought)
                            }
                            if (text.isNotEmpty()) {
                                if (thinking) append("</think>")
                                thinking = false
                                append(text)
                            }
                        }
                        if ((out.isNotEmpty() && !sink.onToken(out)) || tokens >= limit) conv.cancelProcess()
                    }

                    override fun onDone() = done.countDown()

                    override fun onError(throwable: Throwable) {
                        error = throwable
                        done.countDown()
                    }
                },
                thinkingConfig = thinkBudget?.let { ThinkingConfig(enableThinking = true, thinkingTokenBudget = it.toInt()) },
            )
            done.await()
            bench = runCatching { conv.getBenchmarkInfo() }.getOrNull()
            running = null
        }
        error?.let { if (it !is java.util.concurrent.CancellationException) throw CpException.Failed(it.message ?: it.toString()) }
        val end = System.nanoTime()
        val firstAt = if (first == 0L) end else first
        GenStatsRecord(
            // Without the benchmark, estimate the prompt from characters and count one token per message.
            promptTokens = bench?.lastPrefillTokenCount?.toUInt() ?: ((system.length + user.length) / 4).toUInt(),
            genTokens = bench?.lastDecodeTokenCount?.toUInt() ?: tokens,
            prefillMs = (firstAt - start) / 1e6,
            decodeMs = (end - firstAt) / 1e6,
        )
    }
}
