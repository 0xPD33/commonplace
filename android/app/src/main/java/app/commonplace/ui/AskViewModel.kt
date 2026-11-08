package app.commonplace.ui

import android.app.Application
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import app.commonplace.CommonplaceApp
import app.commonplace.engine.Conversations
import app.commonplace.engine.ModelState
import app.commonplace.engine.ReadAloud
import app.commonplace.engine.SavedTurn
import app.commonplace.engine.VoiceInput
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.asFlow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.launch
import uniffi.commonplace_ffi.AnswerCard
import uniffi.commonplace_ffi.AnswerSegment
import uniffi.commonplace_ffi.AskInput
import uniffi.commonplace_ffi.AskListener
import uniffi.commonplace_ffi.AskStage
import uniffi.commonplace_ffi.SourceItem
import uniffi.commonplace_ffi.Timing
import uniffi.commonplace_ffi.TurnKind
import uniffi.commonplace_ffi.TurnRecord

/** One question and its answer as it streams in. All fields are Compose state. */
class Turn(val query: String, val think: Boolean = false) {
    /** Stable list key: a retry repeats the query at the same index. */
    val id = System.nanoTime()
    var thought by mutableStateOf("")
    var thoughtDone by mutableStateOf(false)
    /** When the first reasoning token came, and how long the reasoning took (ms), for "Thought for 14 s". */
    var thinkStart = 0L
    var thinkMs by mutableStateOf<Long?>(null)
    /** Opened from history: it skips the entrance motion of a new answer. */
    var restored = false
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

    fun saved() = SavedTurn(query, think, kind, card, sources, segments, stopped, error)

    companion object {
        fun restore(t: SavedTurn) = Turn(t.query, t.think).apply {
            kind = t.kind
            card = t.card
            sources = t.sources
            segments = t.segments
            stage = AskStage.DONE
            done = true
            answering = false
            stopped = t.stopped
            error = t.error
            restored = true
        }
    }
}

/** The one pack a question is limited to ("Ask this document"). */
class AskScope(val packId: String, val title: String)

private const val MAX_DRAFT_CHARS = 1000
/** The core's limit (`engine::MAX_QUESTION_CHARS`). */
internal const val MAX_QUESTION_CHARS = 4000

private const val KEY_CONVERSATION = "conversation"
private const val KEY_INPUT = "input"
private const val KEY_ASKING = "asking"

class AskViewModel(app: Application, private val restored: SavedStateHandle) : AndroidViewModel(app) {
    private val holder = (app as CommonplaceApp).engine
    private val main = Handler(Looper.getMainLooper())

    val turns = mutableStateListOf<Turn>()
    private val saved = Conversations(app)
    /** The file id of the conversation on screen; a new topic starts a new one. */
    private var conversationId: Long = restored[KEY_CONVERSATION] ?: System.currentTimeMillis()
    private var draft by mutableStateOf(restored[KEY_ASKING] ?: restored[KEY_INPUT] ?: "")
    var input: String
        get() = draft
        set(v) {
            draft = v
            // Voice input writes from its audio thread; SavedStateHandle belongs to the main thread.
            main.post { restored[KEY_INPUT] = v.take(MAX_QUESTION_CHARS) }
        }

    /** The last answer could not be written to storage. It stays on screen. */
    var saveFailed by mutableStateOf(false)
        private set

    // After the system killed the process, the conversation on screen comes back, and a question with no answer yet waits in the box.
    init {
        restored.remove<String>(KEY_ASKING)
        if (restored.contains(KEY_CONVERSATION)) turns += saved.load(conversationId).map(Turn::restore)
    }

    /** While set, every question searches only this pack, follow-ups included, until the user removes it. */
    var scope by mutableStateOf<AskScope?>(null)
        private set

    /** A draft from another app has not yet moved the focus to the question box. */
    var draftWaiting by mutableStateOf(false)

    /** Text that another app sent. It starts a new topic and waits in the box, so the user decides whether to ask. */
    fun receiveDraft(text: String) {
        stopVoice()
        // The recognizer flushes a last line after it stops; it must not overwrite this draft.
        voiceSession++
        if (!busy) {
            newTopic()
            scope = null
        }
        input = text.trim().take(MAX_DRAFT_CHARS)
        draftWaiting = true
    }

    /** Limit the next questions to one pack. A conversation about the whole library starts over. */
    fun searchOnly(packId: String, title: String) {
        if (busy) return
        newTopic()
        scope = AskScope(packId, title)
    }

    fun clearScope() {
        scope = null
    }

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

    private val readAloud = ReadAloud(app)
    /** The id of the turn being read aloud. */
    var speaking by mutableStateOf<Long?>(null)
        private set
    /** Why reading a turn aloud failed: the turn id and the message. */
    var speakError by mutableStateOf<Pair<Long, String>?>(null)
        private set

    /** Read the answer aloud, or stop when it is the one being read. */
    fun toggleSpeak(turn: Turn) {
        val was = speaking
        stopSpeaking()
        // The bundled voice needs CPU and about 0.2 GB, which a model that is still writing may need.
        if (was == turn.id || busy) return
        speaking = turn.id
        speakError = null
        readAloud.speak(spokenText(turn.segments), holder.prefs.bundledVoice) { error ->
            speaking = null
            if (error != null) speakError = turn.id to error
        }
    }

    fun stopSpeaking() {
        if (speaking == null) return
        speaking = null
        readAloud.stop()
    }

    override fun onCleared() {
        readAloud.close()
        voice.stop()
        // The foreground service can keep the process alive; do not leave an answer running nobody sees.
        if (busy) holder.cancelAnswer()
    }

    fun toggleThinking() {
        thinking = !thinking
        holder.prefs.thinking = thinking
    }

    val busy: Boolean
        get() = turns.lastOrNull()?.answering == true

    fun ask(text: String = input, think: Boolean = thinking) {
        val query = text.trim()
        if (query.isEmpty() || query.length > MAX_QUESTION_CHARS || busy) return
        val engine = holder.engineOrNull ?: return
        stopSpeaking()
        if (listening) {
            // Free the speech model before the LLM needs the RAM; its trailing line is dropped.
            listening = false
            voiceSession++
            voice.stop()
        }
        // A chip, Retry or Think it through must not wipe a draft the user is typing.
        if (query == input.trim()) input = ""
        // Small talk carries nothing a later turn needs.
        val history = turns.filter { it.done && it.error == null && it.kind != TurnKind.CHAT }.takeLast(2).map { TurnRecord(it.query, it.answerText, it.sources) }
        val turn = Turn(query, think)
        val packs = listOfNotNull(scope?.packId)
        turns += turn
        restored[KEY_ASKING] = query
        holder.answering = true
        viewModelScope.launch(Dispatchers.IO) {
            try {
                if (holder.model.value !is ModelState.Loaded) {
                    main.post { turn.loadingModel = true }
                }
                holder.ensureModel(null)
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
                        persist()
                    }
                    return@launch
                }
                val startMs = System.currentTimeMillis()
                val energyBefore = holder.power.read()
                val result = engine.ask(AskInput(query, history, think, holder.thermalHeadroom(), packs), Listener(turn))
                holder.power.log(query, (model as? ModelState.Loaded)?.id, startMs, energyBefore, holder.power.read())
                main.post {
                    turn.kind = result.kind
                    turn.card = result.card
                    turn.sources = result.sources
                    turn.segments = result.segments
                    turn.timing = result.timing
                    turn.done = true
                    turn.answering = false
                    // Settings can cancel an answer too, by a change of model. Its text may end anywhere.
                    if (result.cancelled) turn.stopped = true
                    // Only a question reads evidence; thinking adds its own reasoning time before the first word.
                    result.timing?.takeIf { !think && result.kind == TurnKind.QUESTION }?.let { t -> t.ttftMs?.let { holder.prefs.noteRead(it - t.cardMs) } }
                    persist()
                }
            } catch (t: Throwable) {
                main.post {
                    turn.error = t.message ?: t.toString()
                    turn.answering = false
                    turn.done = true
                    persist()
                }
            } finally {
                // The app may have gone off screen during the answer.
                holder.answerEnded()
            }
        }
    }

    /** Ask the same question again, this time with reasoning first, as a new turn under the fast answer. */
    fun thinkThrough(turn: Turn) = ask(turn.query, think = true)

    /** Replace the last answer with a fresh one to the same question. */
    fun retry(turn: Turn) {
        if (busy || turns.lastOrNull() !== turn) return
        turns.removeAt(turns.lastIndex)
        ask(turn.query, turn.think)
    }

    fun stop() {
        turns.lastOrNull()?.takeIf { it.answering }?.stopped = true
        holder.cancelAnswer()
    }

    fun newTopic() {
        if (busy) return
        stopSpeaking()
        turns.clear()
        conversationId = System.currentTimeMillis()
        restored.remove<Long>(KEY_CONVERSATION)
    }

    /** Save the finished turns of the conversation on screen. The process scope: an answer can end after the screen. */
    private fun persist() {
        val id = conversationId
        val done = turns.filter { it.done }.map { it.saved() }
        restored[KEY_CONVERSATION] = id
        restored.remove<String>(KEY_ASKING)
        holder.scope.launch {
            val ok = saved.save(id, done)
            main.post { saveFailed = !ok }
        }
    }

    fun recent(): List<Conversations.Summary> = saved.summaries().take(5).toList().sortedByDescending { it.updated }

    fun historyPages(): Flow<List<Conversations.Summary>> = saved.summaries().chunked(30).asFlow().flowOn(Dispatchers.IO)

    fun open(id: Long) {
        if (busy) return
        val loaded = saved.load(id)
        if (loaded.isEmpty()) return
        stopSpeaking()
        turns.clear()
        turns += loaded.map(Turn::restore)
        conversationId = id
        restored[KEY_CONVERSATION] = id
    }

    fun deleteConversation(id: Long) {
        saved.delete(id)
        if (id == conversationId) newTopic()
    }

    private inner class Listener(private val turn: Turn) : AskListener {
        override fun onStage(stage: AskStage, detail: String) = main.post {
            // A stop that landed while engine.ask was clearing the cancel flag.
            if (turn.stopped) holder.cancelAnswer()
            turn.stage = stage
            turn.stageDetail = detail
        }.let { }

        override fun onCard(card: AnswerCard) = main.post { turn.card = card }.let { }

        override fun onSources(sources: List<SourceItem>) = main.post { turn.sources = sources }.let { }

        override fun onThinking(text: String, done: Boolean) = main.post {
            val now = System.currentTimeMillis()
            if (turn.thinkStart == 0L) turn.thinkStart = now
            if (done && turn.thinkMs == null) turn.thinkMs = now - turn.thinkStart
            turn.thought = text
            turn.thoughtDone = done
        }.let { }

        override fun onAnswer(segments: List<AnswerSegment>, done: Boolean) = main.post { turn.segments = segments }.let { }
    }
}
