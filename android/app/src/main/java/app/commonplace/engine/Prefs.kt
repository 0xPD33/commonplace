package app.commonplace.engine

import android.content.Context
import uniffi.commonplace_ffi.CommonplaceEngine
import uniffi.commonplace_ffi.ModelKind

/** Small persisted preferences. Engine tuning is re-applied at startup. */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("commonplace", Context.MODE_PRIVATE)

    var backend: Backend
        get() = if (sp.getString("backend", "cpu") == "tpu") Backend.Tpu else Backend.Cpu
        set(v) = sp.edit().putString("backend", if (v == Backend.Tpu) "tpu" else "cpu").apply()

    var showTimings: Boolean
        get() = sp.getBoolean("show_timings", true)
        set(v) = sp.edit().putBoolean("show_timings", v).apply()

    /** The everyday model when several are installed. */
    var modelKind: ModelKind
        get() = if (sp.getString("model", "fast") == "small") ModelKind.SMALL else ModelKind.FAST
        set(v) = sp.edit().putString("model", if (v == ModelKind.SMALL) "small" else "fast").apply()

    var threads: Int
        get() = sp.getInt("threads", 4)
        set(v) = sp.edit().putInt("threads", v).apply()

    var evidenceTokens: Int
        get() = sp.getInt("evidence_tokens", 600)
        set(v) = sp.edit().putInt("evidence_tokens", v).apply()

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

    /** Let the model rewrite each question (typos, follow-ups) before searching. */
    var rewrite: Boolean
        get() = sp.getBoolean("rewrite", false)
        set(v) = sp.edit().putBoolean("rewrite", v).apply()

    var usePlanner: Boolean
        get() = sp.getBoolean("planner", true)
        set(v) = sp.edit().putBoolean("planner", v).apply()

    var recent: List<String>
        get() = sp.getString("recent", "")!!.split('\n').filter { it.isNotBlank() }
        set(v) = sp.edit().putString("recent", v.take(12).joinToString("\n")).apply()

    fun remember(query: String) {
        recent = listOf(query) + recent.filter { it != query }
    }

    fun applyTo(engine: CommonplaceEngine) {
        val s = engine.settings()
        engine.updateSettings(
            s.copy(
                llmThreads = threads.toUInt(),
                evidenceTokens = evidenceTokens.toUInt(),
                maxAnswerTokens = answerTokens.toUInt(),
                thinkBudget = thinkBudget.toUInt(),
                rewrite = rewrite,
                usePlanner = usePlanner,
            ),
        )
    }
}
