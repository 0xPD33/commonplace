package app.commonplace.engine

import android.app.ActivityManager
import android.app.UiModeManager
import android.content.Context
import uniffi.commonplace_ffi.CommonplaceEngine
import uniffi.commonplace_ffi.ModelKind

/** Small persisted preferences. Engine tuning is re-applied at startup. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("commonplace", Context.MODE_PRIVATE)

    // Ling needs about 5.5 GB; an 8 GB phone reports about 7.5 GB of total memory.
    val lowRam = ActivityManager.MemoryInfo().also {
        (context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(it)
    }.totalMem < 10_000_000_000L

    /** Tpu runs an installed Gemma pack with LiteRT-LM; without one, every answer uses llama.cpp. */
    var backend: Backend
        get() = if (sp.getString("backend", "tpu") == "tpu") Backend.Tpu else Backend.Cpu
        set(v) = sp.edit().putString("backend", if (v == Backend.Tpu) "tpu" else "cpu").apply()

    /** The LiteRT model file that killed the app while it loaded; the app uses llama.cpp until the user picks Gemma again. */
    var liteCrashed: String?
        get() = sp.getString("lite_crashed", null)
        set(v) = sp.edit().putString("lite_crashed", v).apply()

    /** Speculative decoding for Gemma on the Tensor NPU. */
    var speculative: Boolean
        get() = sp.getBoolean("speculative", true)
        set(v) = sp.edit().putBoolean("speculative", v).apply()

    var showTimings: Boolean
        get() = sp.getBoolean("show_timings", false)
        set(v) = sp.edit().putBoolean("show_timings", v).apply()

    /** The everyday model when several are installed. Without a choice, low-RAM phones get the small one. */
    var modelKind: ModelKind
        get() = if (sp.getString("model", if (lowRam) "small" else "fast") == "small") ModelKind.SMALL else ModelKind.FAST
        set(v) = sp.edit().putString("model", if (v == ModelKind.SMALL) "small" else "fast").apply()

    /** A UiModeManager night mode. The system also persists it; this copy only shows the choice in Settings. */
    var nightMode: Int
        get() = sp.getInt("night_mode", UiModeManager.MODE_NIGHT_AUTO)
        set(v) = sp.edit().putInt("night_mode", v).apply()

    var threads: Int
        get() = sp.getInt("threads", 4)
        set(v) = sp.edit().putInt("threads", v).apply()

    /** The user's evidence budget; null uses the loaded model's default. Until 0.3.0, Settings saved 600 under "evidence_tokens". */
    var evidenceOverride: Int?
        get() = sp.getInt("evidence_override", 0).takeIf { it > 0 }
        set(v) = sp.edit().apply { if (v == null) remove("evidence_override") else putInt("evidence_override", v) }.apply()

    var answerTokens: Int
        get() = sp.getInt("answer_tokens", 400)
        set(v) = sp.edit().putInt("answer_tokens", v).apply()

    /** Thinking mode for new questions: the model reasons first, capped by [thinkBudget]. */
    var thinking: Boolean
        get() = sp.getBoolean("thinking", false)
        set(v) = sp.edit().putBoolean("thinking", v).apply()

    var thinkBudget: Int
        get() = sp.getInt("think_budget", 256)
        set(v) = sp.edit().putInt("think_budget", v).apply()

    /** How long the model reads the evidence before its first word, smoothed over recent answers; it paces the reading rule. */
    var readMs: Int
        get() = sp.getInt("read_ms", 6500)
        set(v) = sp.edit().putInt("read_ms", v).apply()

    fun noteRead(ms: Double) {
        readMs = (0.7 * readMs + 0.3 * ms.coerceIn(1000.0, 30000.0)).toInt()
    }

    /** Let the model rewrite each question (typos, follow-ups) before searching. */
    var rewrite: Boolean
        get() = sp.getBoolean("rewrite", false)
        set(v) = sp.edit().putBoolean("rewrite", v).apply()

    fun applyTo(engine: CommonplaceEngine) {
        val s = engine.settings()
        engine.updateSettings(
            s.copy(
                llmThreads = threads.toUInt(),
                evidenceTokens = evidenceOverride?.toUInt(),
                maxAnswerTokens = answerTokens.toUInt(),
                thinkBudget = thinkBudget.toUInt(),
                rewrite = rewrite,
            ),
        )
    }
}
