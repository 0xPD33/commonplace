package app.commonplace.ui

import android.app.Application
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import app.commonplace.CommonplaceApp
import app.commonplace.engine.Conversations
import app.commonplace.engine.ModelState
import app.commonplace.engine.SavedTurn
import app.commonplace.engine.VoiceInput
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import uniffi.commonplace_ffi.AnswerCard
import uniffi.commonplace_ffi.AnswerSegment
import uniffi.commonplace_ffi.AskInput
import uniffi.commonplace_ffi.AskListener
import uniffi.commonplace_ffi.AskStage
import uniffi.commonplace_ffi.ModelKind
import uniffi.commonplace_ffi.SourceItem
import uniffi.commonplace_ffi.Timing
import uniffi.commonplace_ffi.TurnKind
import uniffi.commonplace_ffi.TurnRecord

/** One question and its answer as it streams in. All fields are Compose state. */
class Turn(val query: String, val deep: Boolean, val think: Boolean = false) {
    /** Stable list key: a retry repeats the query at the same index. */
    val id = System.nanoTime()
    var thought by mutableStateOf("")
    var thoughtDone by mutableStateOf(false)
    var stage by mutableStateOf(AskStage.SEARCHING)
    var stageDetail by mutableStateOf("")
    var card by mutableStateOf<AnswerCard?>(null)
    var sources by mutableStateOf<List<SourceItem>>(emptyList())
    var segments by mutableStateOf<List<AnswerSegment>>(emptyList())
    var done by mutableStateOf(false)
    var answering by mutableStateOf(true)
    var error by mutableStateOf<String?>(null)
    var timing by mutableStateOf<Timing?>(null)
    var searchOnly by mutableStateOf(false)
    var loadingModel by mutableStateOf(false)
    var stopped by mutableStateOf(false)
    var modelError by mutableStateOf<String?>(null)
    var kind by mutableStateOf(TurnKind.QUESTION)

    val answerText: String
        get() = segments.joinToString("") {
            when (it) {
                is AnswerSegment.Text -> it.text
                is AnswerSegment.Unverified -> it.text
                is AnswerSegment.Cite -> "[${it.n}]"
            }
        }

    /** Citation numbers resolve against [sources] once evidence is numbered, else the card list. */
    fun source(n: UInt): SourceItem? = sources.firstOrNull { it.n == n } ?: card?.sources?.getOrNull(n.toInt() - 1)

    fun saved() = SavedTurn(query, deep, think, kind, card, sources, segments)

    companion object {
        fun restore(t: SavedTurn) = Turn(t.query, t.deep, t.think).apply {
            kind = t.kind
            card = t.card
            sources = t.sources
            segments = t.segments
            stage = AskStage.DONE
            done = true
            answering = false
        }
    }
}

class AskViewModel(app: Application) : AndroidViewModel(app) {
    private val holder = (app as CommonplaceApp).engine
    private val main = Handler(Looper.getMainLooper())

    val turns = mutableStateListOf<Turn>()
    private val saved = Conversations(app)
    /** The file id of the conversation on screen; a new topic starts a new one. */
    private var conversationId = System.currentTimeMillis()
    var input by mutableStateOf("")
    var thinking by mutableStateOf(holder.prefs.thinking)
        private set

    private val voice = VoiceInput(app)
    val voiceAvailable: Boolean get() = voice.installed
    var listening by mutableStateOf(false)
        private set
    var voiceError by mutableStateOf<String?>(null)
        private set
    private var typed = ""
    private val heard = mutableListOf<String>()
    private var live = ""
    private var voiceSession = 0

    /** Tap to talk, tap to stop. The transcript fills [input], where the user can edit it before sending. */
    fun toggleVoice() {
        if (listening) {
            listening = false
            voice.stop()
            return
        }
        if (busy || !voice.installed) return
        typed = input.trim()
        heard.clear()
        live = ""
        voiceError = null
        listening = true
        val session = ++voiceSession
        // Callbacks from an older session (or after a send discarded it) must not rewrite the input.
        viewModelScope.launch(Dispatchers.IO) {
            try {
                voice.start(
                    onText = { if (session == voiceSession) { live = it; showTranscript() } },
                    onLine = { if (session == voiceSession) { heard += it.trim(); live = ""; showTranscript() } },
                    onError = { if (session == voiceSession) voiceFailed(it) },
                )
            } catch (t: Throwable) {
                main.post { if (session == voiceSession) voiceFailed(t) }
            }
        }
    }

    /** Stop listening, e.g. when the screen goes away. The last words still land in [input]. */
    fun stopVoice() {
        if (listening) toggleVoice()
    }

    fun micDenied() {
        voiceError = "microphone permission denied (allow it in the app's system settings)"
    }

    private fun showTranscript() {
        input = listOf(typed, heard.joinToString(" "), live.trim()).filter { it.isNotEmpty() }.joinToString(" ")
    }

    private fun voiceFailed(t: Throwable) {
        listening = false
        voiceError = t.message ?: t.toString()
        voice.stop()
    }

    override fun onCleared() {
        voice.stop()
        // The foreground service can keep the process alive; do not leave an answer running nobody sees.
        if (busy) holder.engineOrNull?.cancel()
    }

    fun toggleThinking() {
        thinking = !thinking
        holder.prefs.thinking = thinking
    }

    val busy: Boolean
        get() = turns.lastOrNull()?.answering == true

    fun ask(text: String = input, deep: Boolean = false, think: Boolean = thinking && !deep) {
        val query = text.trim()
        if (query.isEmpty() || busy) return
        val engine = holder.engineOrNull ?: return
        if (listening) {
            // Free the speech model before the LLM needs the RAM; its trailing line is dropped.
            listening = false
            voiceSession++
            voice.stop()
        }
        // A chip, Retry or Think harder must not wipe a draft the user is typing.
        if (query == input.trim()) input = ""
        holder.prefs.remember(query)
        // Small talk carries nothing a later turn needs.
        val history = turns.filter { it.done && it.error == null && it.kind != TurnKind.CHAT }.takeLast(2).map { TurnRecord(it.query, it.answerText, it.sources) }
        val turn = Turn(query, deep, think)
        turns += turn
        viewModelScope.launch(Dispatchers.IO) {
            try {
                val wanted = if (deep) ModelKind.DEEP else null
                val loaded = holder.model.value as? ModelState.Loaded
                if (loaded == null || (deep && loaded.kind != ModelKind.DEEP) || (!deep && loaded.kind == ModelKind.DEEP)) {
                    main.post { turn.loadingModel = true }
                }
                holder.ensureModel(wanted)
                val model = holder.model.value
                main.post {
                    turn.loadingModel = false
                    turn.searchOnly = model !is ModelState.Loaded
                    turn.modelError = (model as? ModelState.Failed)?.message
                }
                // engine.ask clears the cancel flag, so a stop during the model load must be checked here.
                if (turn.stopped) {
                    main.post {
                        turn.answering = false
                        turn.done = true
                    }
                    return@launch
                }
                val result = engine.ask(AskInput(query, history, deep, think, holder.thermalHeadroom()), Listener(turn))
                main.post {
                    turn.kind = result.kind
                    turn.card = result.card
                    turn.sources = result.sources
                    turn.segments = result.segments
                    turn.timing = result.timing
                    turn.done = true
                    turn.answering = false
                    persist()
                }
            } catch (t: Throwable) {
                main.post {
                    turn.error = t.message ?: t.toString()
                    turn.answering = false
                    turn.done = true
                }
            }
        }
    }

    /** Re-ask a question with the big model (deep mode). */
    fun thinkHarder(turn: Turn) = ask(turn.query, deep = true)

    /** Replace the last answer with a fresh one to the same question. */
    fun retry(turn: Turn) {
        if (busy || turns.lastOrNull() !== turn) return
        turns.removeAt(turns.lastIndex)
        ask(turn.query, turn.deep, turn.think)
    }

    fun stop() {
        turns.lastOrNull()?.takeIf { it.answering }?.stopped = true
        holder.engineOrNull?.cancel()
    }

    fun newTopic() {
        if (busy) return
        turns.clear()
        conversationId = System.currentTimeMillis()
    }

    /** Save the finished turns of the conversation on screen. */
    private fun persist() {
        val id = conversationId
        val done = turns.filter { it.done && it.error == null && !it.stopped }.map { it.saved() }
        viewModelScope.launch(Dispatchers.IO) { saved.save(id, done) }
    }

    fun history(): List<Conversations.Summary> = saved.list()

    fun open(id: Long) {
        if (busy) return
        val loaded = saved.load(id)
        if (loaded.isEmpty()) return
        turns.clear()
        turns += loaded.map(Turn::restore)
        conversationId = id
    }

    fun deleteConversation(id: Long) {
        saved.delete(id)
        if (id == conversationId) newTopic()
    }

    private inner class Listener(private val turn: Turn) : AskListener {
        override fun onStage(stage: AskStage, detail: String) = main.post {
            // A stop that landed while engine.ask was clearing the cancel flag.
            if (turn.stopped) holder.engineOrNull?.cancel()
            turn.stage = stage
            turn.stageDetail = detail
        }.let { }

        override fun onCard(card: AnswerCard) = main.post { turn.card = card }.let { }

        override fun onSources(sources: List<SourceItem>) = main.post { turn.sources = sources }.let { }

        override fun onThinking(text: String, done: Boolean) = main.post {
            turn.thought = text
            turn.thoughtDone = done
        }.let { }

        override fun onAnswer(segments: List<AnswerSegment>, done: Boolean) = main.post { turn.segments = segments }.let { }
    }
}
