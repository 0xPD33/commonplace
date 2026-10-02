package app.commonplace.engine

import ai.moonshine.voice.JNI
import ai.moonshine.voice.MicTranscriber
import android.content.Context
import android.os.Handler
import android.os.Looper
import java.io.File

/**
 * Tap-to-talk speech input with Moonshine Medium Streaming (English, MIT).
 *
 * The model files come from `filesDir/stt/moonshine-medium-streaming-en` (scripts/dev-push.sh stt),
 * loaded with loadFromFiles so the library's downloader never runs. The model holds ~0.75 GB while
 * loaded, so [stop] frees it as soon as the last line is flushed, before the LLM needs the RAM.
 * Callbacks run on the main thread.
 */
class VoiceInput(context: Context) {
    private val app = context.applicationContext
    private val modelDir = File(app.filesDir, "stt/moonshine-medium-streaming-en")
    private val main = Handler(Looper.getMainLooper())
    private val lock = Any()

    // All three guarded by [lock]. stop() bumps [session] so a start() still loading the model gives up.
    private var mic: MicTranscriber? = null
    private val flushing = mutableSetOf<MicTranscriber>()
    private var session = 0

    val installed: Boolean get() = File(modelDir, "encoder.ort").isFile

    /** Loads the model (mapped from flash) and opens the mic. Blocks: call off the main thread. */
    fun start(onText: (String) -> Unit, onLine: (String) -> Unit, onError: (Throwable) -> Unit) {
        val id = synchronized(lock) { ++session }
        lateinit var m: MicTranscriber
        m = MicTranscriber(app)
            .onText { onText(it) }
            .onLine {
                onLine(it.text)
                close(m) // the line that lands after stop() is the flush; a no-op while listening
            }
            .onError(onError)
        m.loadFromFiles(modelDir.path, JNI.MOONSHINE_MODEL_ARCH_MEDIUM_STREAMING)
        synchronized(lock) {
            if (id == session) {
                m.start()
                mic = m
                return
            }
        }
        Thread { m.close() }.start() // stopped while the model loaded
    }

    /** Ends capture. The trailing line arrives through `onLine`, then the model is freed. */
    fun stop() {
        val m = synchronized(lock) {
            session++
            val cur = mic ?: return
            mic = null
            flushing += cur
            cur
        }
        m.stop()
        main.postDelayed({ close(m) }, FLUSH_TIMEOUT_MS) // no trailing line came: free anyway
    }

    private fun close(m: MicTranscriber) {
        if (!synchronized(lock) { flushing.remove(m) }) return
        Thread { m.close() }.start() // close() joins the audio thread for up to 1 s
    }

    private companion object {
        const val FLUSH_TIMEOUT_MS = 1_000L
    }
}
