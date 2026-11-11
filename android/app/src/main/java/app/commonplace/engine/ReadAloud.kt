package app.commonplace.engine

import android.content.Context
import android.icu.text.MeasureFormat
import android.icu.text.MessageFormat
import android.icu.util.Currency
import android.icu.util.CurrencyAmount
import android.icu.util.Measure
import android.icu.util.MeasureUnit
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.system.Os
import android.util.Log
import android.view.textclassifier.TextClassificationManager
import android.view.textclassifier.TextLanguage
import java.io.File
import java.math.BigDecimal
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Reads an answer aloud on the device, with the system engine or the bundled voice.
 *
 * The system engine only uses a voice that needs no network, for the language of the answer. The bundled voice is
 * Phonon (English) from the `tts-en` pack, always with the pack's default voice. It holds about 0.2 GB and loads in
 * under 0.2 s, so it loads for each answer it reads and frees the memory for the LLM. `onDone` runs on the main
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

    // Guarded by [bundledLock]: the model takes one call at a time.
    private val bundledLock = Any()
    @Volatile private var closed = false

    val bundledInstalled: Boolean get() = File(dir, "model.q8.gguf").isFile

    /** Starts reading [text]; a message comes back through [onDone] if it cannot. */
    fun speak(text: String, bundled: Boolean, onDone: (error: String?) -> Unit) {
        val id = synchronized(lock) { ++session }
        Thread {
            val error = runCatching {
                val lang = language(text)
                if (bundled && bundledInstalled && lang.language == "en") speakBundled(id, spellNumbers(text, lang)) else speakSystem(id, text, lang)
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
        closed = true
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
        synchronized(bundledLock) {
            if (closed || !current(id)) return null
            val tts = try {
                val start = SystemClock.elapsedRealtime()
                // Only for Phonon's own rayon pool: the Rust core sizes its pool from the same variable at its first search.
                // ponytail: a first search during the first reading still gets 2 threads; it only searches slower.
                Os.setenv("RAYON_NUM_THREADS", "2", true)
                PhononTTS(dir.path, "en").also {
                    Log.i(TAG, "Phonon loaded in ${SystemClock.elapsedRealtime() - start} ms")
                }
            } catch (e: LinkageError) {
                // No libptts_ffi.so for this ABI: only arm64 builds ship it.
                Os.unsetenv("RAYON_NUM_THREADS")
                return "the bundled voice is not available on this device. Choose System in Settings."
            } catch (e: Throwable) {
                Os.unsetenv("RAYON_NUM_THREADS")
                throw e
            }
            val t = try {
                AudioTrack.Builder()
                    .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
                    .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_FLOAT).setSampleRate(RATE).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .build()
            } catch (e: Throwable) {
                tts.close()
                Os.unsetenv("RAYON_NUM_THREADS")
                throw e
            }
            synchronized(lock) {
                if (id != session) {
                    t.release()
                    tts.close()
                    Os.unsetenv("RAYON_NUM_THREADS")
                    return null
                }
                track = t
            }
            try {
                t.play()
                var frames = 0
                // Phonon calls back with each piece of audio, and false stops it. Waiting for room in the track paces
                // synthesis to playback; a blocking write could hang on a track that stop() paused.
                val r = tts.speak(text) chunk@{ samples ->
                    var off = 0
                    while (off < samples.size) {
                        if (!current(id)) return@chunk false
                        val n = t.write(samples, off, samples.size - off, AudioTrack.WRITE_NON_BLOCKING)
                        if (n < 0) return@chunk false
                        off += n
                        if (off < samples.size) Thread.sleep(20)
                    }
                    frames += samples.size
                    true
                }
                Log.i(TAG, "first audio after ${r.ttfaMs.toLong()} ms, ${r.samples} samples in ${r.totalMs.toLong()} ms")
                // stop() plays out what is queued, but resets the head position, so the wait is measured before it.
                val end = System.currentTimeMillis() + 1000L * (frames - t.playbackHeadPosition) / RATE + 200
                t.stop()
                while (current(id) && System.currentTimeMillis() < end) Thread.sleep(50)
            } finally {
                synchronized(lock) { if (track === t) track = null }
                t.release()
                tts.close()
                Os.unsetenv("RAYON_NUM_THREADS")
            }
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

    private companion object {
        const val TAG = "ReadAloud"

        /** Phonon's output: 24 kHz mono. */
        const val RATE = 24000
    }
}

/** A number token: a dollar sign, digits with thousands commas and decimals, then an ordinal suffix or a unit. */
private val NUMBER = Regex("""(\$)?\b(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(st|nd|rd|th)?\b(?:\s?(%|°[CF]|(?:km|kg|cm|mm|m)\b))?""")
private val UNITS = mapOf(
    "%" to MeasureUnit.PERCENT, "°C" to MeasureUnit.CELSIUS, "°F" to MeasureUnit.FAHRENHEIT, "km" to MeasureUnit.KILOMETER,
    "m" to MeasureUnit.METER, "cm" to MeasureUnit.CENTIMETER, "mm" to MeasureUnit.MILLIMETER, "kg" to MeasureUnit.KILOGRAM,
)
private val DIGITS = Regex("""[\d.,]*\d""")

/**
 * Numbers in words for the bundled voice. Phonon has its own English normalizer, not yet checked against this step.
 * ICU spells them out: a plain 4-digit number from 1000 to 2099 reads as a year ("nineteen thirty"), and a unit or
 * dollar amount gets its full name.
 * The SDK hides RuleBasedNumberFormat, so its rule sets come through MessageFormat's `spellout` argument.
 * The system engine normalizes numbers itself.
 */
private fun spellNumbers(text: String, locale: Locale): String {
    val words = MessageFormat("{0,spellout}", locale)
    val year = MessageFormat("{0,spellout,%spellout-numbering-year}", locale)
    val ordinal = MessageFormat("{0,spellout,%spellout-ordinal}", locale)
    val measures = MeasureFormat.getInstance(locale, MeasureFormat.FormatWidth.WIDE)
    return NUMBER.replace(text) { m ->
        val (sign, whole, frac, suffix, unit) = m.destructured
        val digits = whole.replace(",", "")
        if (digits.length > 15) return@replace m.value
        val n = BigDecimal(if (frac.isEmpty()) digits else "$digits.$frac")
        val said = words.format(arrayOf(n))
        when {
            suffix.isNotEmpty() -> ordinal.format(arrayOf(digits.toLong()))
            sign.isNotEmpty() || unit.isNotEmpty() -> {
                val q = if (sign.isNotEmpty()) CurrencyAmount(n, Currency.getInstance("USD")) else Measure(n, UNITS.getValue(unit))
                DIGITS.replaceFirst(measures.format(q), Regex.escapeReplacement(said))
            }
            frac.isEmpty() && ',' !in whole && digits.length == 4 && n.toInt() in 1000..2099 -> year.format(arrayOf(digits.toLong()))
            else -> said
        }
    }
}
