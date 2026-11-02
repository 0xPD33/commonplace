package app.commonplace.engine

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.view.textclassifier.TextClassificationManager
import android.view.textclassifier.TextLanguage
import com.k2fsa.sherpa.onnx.GenerationConfig
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsPocketModelConfig
import com.k2fsa.sherpa.onnx.WaveReader
import java.io.File
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Reads an answer aloud on the device, with the system engine or the bundled voice.
 *
 * The system engine only uses a voice that needs no network, for the language of the answer. The bundled voice is
 * Pocket TTS (English) from the `tts-en` pack, run by sherpa-onnx, always with the pack's own voice clip. It holds
 * about 0.6 GB while it speaks, so it loads for each answer and is freed at the end. `onDone` runs on the main
 * thread, and never for a stopped answer.
 */
class ReadAloud(context: Context) {
    private val app = context.applicationContext
    private val dir = File(app.filesDir, "library/packs/tts-en")
    private val main = Handler(Looper.getMainLooper())
    private val lock = Any()

    // Both guarded by [lock]. stop() bumps [session], so the answer that is playing ends at its next sample block.
    private var session = 0
    private var track: AudioTrack? = null
    @Volatile private var system: TextToSpeech? = null

    val bundledInstalled: Boolean get() = File(dir, "lm_main.int8.onnx").isFile

    /** Starts reading [text]; a message comes back through [onDone] if it cannot. */
    fun speak(text: String, bundled: Boolean, onDone: (error: String?) -> Unit) {
        val id = synchronized(lock) { ++session }
        Thread {
            val error = runCatching {
                val lang = language(text)
                if (bundled && bundledInstalled && lang.language == "en") speakBundled(id, text) else speakSystem(id, text, lang)
            }.getOrElse { it.message ?: it.toString() }
            main.post { if (synchronized(lock) { id == session }) onDone(error) }
        }.start()
    }

    fun stop() {
        val t = synchronized(lock) {
            session++
            track
        }
        system?.stop()
        t?.runCatching { pause(); flush() }
    }

    fun close() {
        stop()
        system?.shutdown()
        system = null
    }

    private fun current(id: Int) = synchronized(lock) { id == session }

    /** The language of the answer from Android's on-device detector, else the phone's language. */
    private fun language(text: String): Locale {
        val phone = Locale.getDefault()
        val found = runCatching {
            val r = app.getSystemService(TextClassificationManager::class.java).textClassifier
                .detectLanguage(TextLanguage.Request.Builder(text).build())
            r.getLocale(0).takeIf { r.getConfidenceScore(it) >= 0.5f }?.toLocale()
        }.getOrNull() ?: return phone
        return if (found.language == phone.language) phone else found
    }

    private fun speakSystem(id: Int, text: String, lang: Locale): String? {
        val t = synchronized(this) {
            system ?: run {
                val ready = CountDownLatch(1)
                var status = TextToSpeech.ERROR
                val t = TextToSpeech(app) { status = it; ready.countDown() }
                if (!ready.await(10, TimeUnit.SECONDS) || status != TextToSpeech.SUCCESS) {
                    t.shutdown()
                    return "the phone's text-to-speech engine did not start"
                }
                t.also { system = it }
            }
        }
        val voice = t.voices.orEmpty()
            .filter { !it.isNetworkConnectionRequired && it.locale.language == lang.language && TextToSpeech.Engine.KEY_FEATURE_NOT_INSTALLED !in it.features }
            .maxByOrNull { (if (it.locale.country == lang.country) 1000 else 0) + it.quality }
            ?: return "the phone has no offline voice for ${lang.displayLanguage}. Add one in the system's text-to-speech settings" +
                if (lang.language == "en" && !bundledInstalled) ", or install the Read aloud voice pack in Library." else "."
        t.voice = voice
        val chunks = chunks(text, TextToSpeech.getMaxSpeechInputLength())
        val done = CountDownLatch(1)
        var error: String? = null
        // The listener is shared: the flush of an older answer reports its own utterances here too.
        t.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String) {}
            override fun onDone(utteranceId: String) { if (utteranceId == "$id-${chunks.lastIndex}") done.countDown() }
            override fun onStop(utteranceId: String, interrupted: Boolean) { if (utteranceId.startsWith("$id-")) done.countDown() }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String) = onError(utteranceId, TextToSpeech.ERROR)
            override fun onError(utteranceId: String, errorCode: Int) {
                if (!utteranceId.startsWith("$id-")) return
                error = "the phone's voice failed (error $errorCode)"
                done.countDown()
            }
        })
        if (!current(id)) return null
        chunks.forEachIndexed { i, c -> t.speak(c, if (i == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, "$id-$i") }
        while (!done.await(250, TimeUnit.MILLISECONDS)) if (!current(id)) return null
        return error
    }

    private fun speakBundled(id: Int, text: String): String? {
        val model = OfflineTtsModelConfig(
            pocket = OfflineTtsPocketModelConfig(
                lmFlow = "$dir/lm_flow.int8.onnx",
                lmMain = "$dir/lm_main.int8.onnx",
                encoder = "$dir/encoder.onnx",
                decoder = "$dir/decoder.int8.onnx",
                textConditioner = "$dir/text_conditioner.onnx",
                vocabJson = "$dir/vocab.json",
                tokenScoresJson = "$dir/token_scores.json",
            ),
            numThreads = 2,
        )
        val voice = WaveReader.readWave("$dir/voice.wav")
        val tts = OfflineTts(config = OfflineTtsConfig(model = model))
        try {
            val rate = tts.sampleRate()
            val t = AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT).setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build()
            synchronized(lock) {
                if (id != session) return null.also { t.release() }
                track = t
            }
            try {
                t.play()
                var frames = 0
                // Sherpa calls back with each piece of audio. Waiting for room in the track paces synthesis to playback;
                // a blocking write could hang on a track that stop() paused.
                // Temperature 0.3 instead of 0.7 misread fewer words in the desktop round trip (scripts/e2e_tts.py).
                val config = GenerationConfig(numSteps = 5, referenceAudio = voice.samples, referenceSampleRate = voice.sampleRate, extra = mapOf("temperature" to "0.3"))
                tts.generateWithConfigAndCallback(text, config) { samples ->
                    var off = 0
                    while (off < samples.size) {
                        if (!current(id)) return@generateWithConfigAndCallback 0
                        val n = t.write(samples, off, samples.size - off, AudioTrack.WRITE_NON_BLOCKING)
                        if (n < 0) return@generateWithConfigAndCallback 0
                        off += n
                        if (off < samples.size) Thread.sleep(20)
                    }
                    frames += samples.size
                    1
                }
                t.stop() // plays out what is queued
                val end = System.currentTimeMillis() + 1000L * (frames - t.playbackHeadPosition) / rate + 1000
                while (current(id) && t.playbackHeadPosition < frames && System.currentTimeMillis() < end) Thread.sleep(50)
            } finally {
                synchronized(lock) { if (track === t) track = null }
                t.release()
            }
        } finally {
            tts.release()
        }
        return null
    }

    /** Pieces of at most [max] characters, cut at line ends. */
    private fun chunks(text: String, max: Int): List<String> {
        val out = mutableListOf(StringBuilder())
        for (line in text.lines()) {
            for (part in line.chunked(max)) {
                if (out.last().length + part.length + 1 > max) out += StringBuilder()
                out.last().append(part).append('\n')
            }
        }
        return out.map { it.toString() }.filter { it.isNotBlank() }
    }
}
