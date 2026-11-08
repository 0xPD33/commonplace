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
import com.google.ai.edge.litertlm.InputData
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.MessageCallback
import com.google.ai.edge.litertlm.ResponseCallback
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.Session
import com.google.ai.edge.litertlm.SessionConfig
import com.google.ai.edge.litertlm.ThinkingConfig
import uniffi.commonplace_ffi.CpException
import uniffi.commonplace_ffi.ForeignLlm
import uniffi.commonplace_ffi.GenStatsRecord
import uniffi.commonplace_ffi.TokenSink
import android.util.Log
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
    @Volatile private var runningSession: Session? = null
    /** A cancelled generate may end with any error. It is a stop, not a failure. */
    @Volatile private var cancelRequested = false

    /** A session whose prompt start runs through the NPU while the Rust core searches ([prepare]). */
    private class Prepared(val system: String, val prefix: String, val session: Session, val thread: Thread) {
        @Volatile var error: Throwable? = null
        @Volatile var prefillMs = 0.0
    }
    @Volatile private var prepared: Prepared? = null

    // Gemma's chat template around the system and user text, rendered by LiteRT itself in [initialize].
    private var systemStart = ""
    private var systemEnd = ""
    private var userStart = ""
    private var userEnd = ""
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
        val e = Engine(EngineConfig(modelPath = modelPath, backend = backend, cacheDir = context.cacheDir.path)).also { it.initialize() }
        engine = e
        e.createConversation(ConversationConfig(systemInstruction = Contents.of(MARK))).use { c ->
            val preface = c.renderPrefaceIntoString()
            // The first message renders as the whole prompt: preface, user turn and the model turn's start.
            val user = c.renderMessageIntoString(Message.user(USER_MARK), emptyMap()).substringAfter(preface)
            systemStart = preface.substringBefore(MARK)
            systemEnd = preface.substringAfter(MARK)
            userStart = user.substringBefore(USER_MARK)
            userEnd = user.substringAfter(USER_MARK)
            Log.i(TAG, "template: ${listOf(systemStart, systemEnd, userStart, userEnd).joinToString(" | ") { it.replace("\n", "\\n") }}")
        }
    }

    override fun prepare(system: String, userPrefix: String, temperature: Float, topP: Float) {
        val e = engine ?: return
        if (backendName != "npu") return
        // This call has no error result, so an exception would panic in the core. Without it, generate only takes longer.
        try {
            discardPrepared()
            val session = e.createSession(sessionConfig(temperature, topP))
            val text = systemStart + system.trim() + systemEnd + userStart + userPrefix
            lateinit var p: Prepared
            val thread = Thread {
                val t = System.nanoTime()
                runCatching { session.runPrefill(listOf(InputData.Text(text))) }.onFailure { p.error = it }
                p.prefillMs = (System.nanoTime() - t) / 1e6
                Log.i(TAG, "prepare: prefilled ${text.length} chars in ${p.prefillMs.toInt()} ms")
            }
            p = Prepared(system, userPrefix, session, thread)
            prepared = p
            thread.start()
        } catch (t: Throwable) {
            Log.w(TAG, "prepare failed", t)
        }
    }

    private fun discardPrepared() {
        val p = prepared ?: return
        prepared = null
        p.thread.join()
        p.session.close()
    }

    private fun sessionConfig(temperature: Float, topP: Float) = SessionConfig(
        samplerConfig = SamplerConfig(topK = 40, topP = topP.toDouble(), temperature = temperature.toDouble().coerceAtLeast(0.01)),
        enableSpeculativeDecoding = speculative && backendName == "npu",
    )

    /** Stop the running generate. It may have closed its conversation in this moment, so a failure here means nothing. */
    fun cancelRunning() {
        cancelRequested = true
        runCatching { running?.cancelProcess() }
        runCatching { runningSession?.cancelProcess() }
    }

    fun close() {
        cancelRunning()
        synchronized(lock) {
            runCatching {
                discardPrepared()
                engine?.close()
            }.onFailure { Log.w(TAG, "close failed", it) }
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
        cancelRequested = false
        val p = prepared
        if (p != null) {
            prepared = null
            p.thread.join()
            if (p.error == null && thinkBudget == null && !jsonOnly && p.system == system && user.startsWith(p.prefix)) {
                return@synchronized resume(p, user.substring(p.prefix.length), maxTokens, sink)
            }
            p.error?.let { Log.w(TAG, "prepare failed", it) }
            p.session.close()
        }
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
        var truncated = false
        // Reasoning tokens do not count against the answer's limit, as with llama.cpp.
        val limit = maxTokens + (thinkBudget ?: 0u)
        val done = CountDownLatch(1)
        e.createConversation(cfg).use { conv ->
            running = conv
            try {
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
                        val more = out.isEmpty() || sink.onToken(out)
                        truncated = tokens >= limit
                        if (!more || truncated) conv.cancelProcess()
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
            } finally {
                running = null
            }
        }
        error?.let { if (!cancelRequested && !truncated && it !is java.util.concurrent.CancellationException) throw CpException.Failed(it.message ?: it.toString()) }
        val end = System.nanoTime()
        val firstAt = if (first == 0L) end else first
        GenStatsRecord(
            // Without the benchmark, estimate the prompt from characters and count one token per message.
            promptTokens = bench?.lastPrefillTokenCount?.toUInt() ?: ((system.length + user.length) / 4).toUInt(),
            genTokens = bench?.lastDecodeTokenCount?.toUInt() ?: tokens,
            prefillMs = (firstAt - start) / 1e6,
            decodeMs = (end - firstAt) / 1e6,
            truncated = truncated,
        )
    }

    /** The rest of the user turn on top of a [prepare]d session, then the answer. */
    private fun resume(p: Prepared, rest: String, maxTokens: UInt, sink: TokenSink): GenStatsRecord = p.session.use { s ->
        val text = rest + userEnd
        val start = System.nanoTime()
        var first = 0L
        var tokens = 0u
        var error: Throwable? = null
        var truncated = false
        val done = CountDownLatch(1)
        runningSession = s
        try {
        s.generateContentStream(
            listOf(InputData.Text(text)),
            object : ResponseCallback {
                override fun onNext(response: String) {
                    if (first == 0L) first = System.nanoTime()
                    tokens++
                    val more = response.isEmpty() || sink.onToken(response)
                    truncated = tokens >= maxTokens
                    if (!more || truncated) s.cancelProcess()
                }

                override fun onDone() = done.countDown()

                override fun onError(throwable: Throwable) {
                    error = throwable
                    done.countDown()
                }
            },
        )
        done.await()
        } finally {
            runningSession = null
        }
        error?.let { if (!cancelRequested && !truncated && it !is java.util.concurrent.CancellationException) throw CpException.Failed(it.message ?: it.toString()) }
        val end = System.nanoTime()
        val firstAt = if (first == 0L) end else first
        Log.i(TAG, "resume: prepared prefill ${p.prefillMs.toInt()} ms, rest to first token ${((firstAt - start) / 1e6).toInt()} ms, $tokens chunks")
        GenStatsRecord(
            // The session API has no benchmark counter: estimate the prompt from characters, one token per chunk.
            promptTokens = ((p.system.length + p.prefix.length + rest.length) / 4).toUInt(),
            genTokens = tokens,
            prefillMs = (firstAt - start) / 1e6,
            decodeMs = (end - firstAt) / 1e6,
            truncated = truncated,
        )
    }

    private companion object {
        const val TAG = "LiteRtBackend"
        const val MARK = "@@COMMONPLACE@@"
        const val USER_MARK = "@@COMMONPLACE_USER@@"
    }
}
