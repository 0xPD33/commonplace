package app.commonplace.engine

import android.app.ActivityManager
import android.app.Application
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build
import android.os.PowerManager
import android.util.Log
import app.commonplace.BuildConfig
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import uniffi.commonplace_ffi.CommonplaceEngine
import uniffi.commonplace_ffi.EngineOptions
import uniffi.commonplace_ffi.LibraryInfo
import uniffi.commonplace_ffi.ModelKind
import uniffi.commonplace_ffi.ModelPackInfo
import java.io.File
import kotlin.math.exp

sealed interface EngineState {
    data object Starting : EngineState
    data class Ready(val engine: CommonplaceEngine) : EngineState
    data class Failed(val message: String) : EngineState
}

sealed interface ModelState {
    /** No model pack installed: the app answers with the search card only. */
    data object None : ModelState
    data class Loading(val kind: ModelKind?) : ModelState
    data class Loaded(val kind: ModelKind?, val id: String, val backend: Backend) : ModelState
    data class Failed(val message: String) : ModelState
}

enum class Backend { Cpu, Tpu }

/** How far a model load is. [percent] is set only while llama.cpp reports the weights it has read. */
data class LoadProgress(val fraction: Float, val percent: Int?)

/** An answer model, not a voice pack. */
val ModelPackInfo.answers: Boolean get() = kind == ModelKind.FAST || kind == ModelKind.SMALL

/** Owns the Rust engine for the whole process. All engine calls run off the main thread. */
class EngineHolder(private val app: Application) {
    /** Process-wide work that must outlive a screen, such as an import that has to refresh the library. */
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val modelLock = Mutex()
    val prefs = Prefs(app)
    val power = PowerMeter(app)

    private val _state = MutableStateFlow<EngineState>(EngineState.Starting)
    val state: StateFlow<EngineState> = _state.asStateFlow()

    private val _library = MutableStateFlow<LibraryInfo?>(null)
    val library: StateFlow<LibraryInfo?> = _library.asStateFlow()

    private val _model = MutableStateFlow<ModelState>(ModelState.None)
    val model: StateFlow<ModelState> = _model.asStateFlow()

    private val _loadProgress = MutableStateFlow<LoadProgress?>(null)
    val loadProgress: StateFlow<LoadProgress?> = _loadProgress.asStateFlow()

    @Volatile private var liteRt: LiteRtBackend? = null
    /** The Gemma pack failed to start (another chip, or no NPU access): answer with llama.cpp until the next reload. */
    private var liteFailed = false
    /** Exists while a model loads and holds its path. A process that dies there leaves it, so the next start finds it. */
    private val loadingMarker = File(app.filesDir, "litert-loading")

    val engineOrNull: CommonplaceEngine?
        get() = (state.value as? EngineState.Ready)?.engine

    /** The engine, once it has started. A restored screen can ask before it has. Throws when the start failed. */
    suspend fun awaitEngine(): CommonplaceEngine = when (val s = state.first { it !is EngineState.Starting }) {
        is EngineState.Ready -> s.engine
        is EngineState.Failed -> error(s.message)
        EngineState.Starting -> error("The engine is starting")
    }

    /** Start again after a failed start, for example when the user has freed up storage. */
    fun retryStart() {
        _state.value = EngineState.Starting
        start()
    }

    /** A pack install or a document index runs. The foreground service keeps the process alive until it ends. */
    @Volatile
    var installing = false
        private set

    fun installStarted(what: String) {
        installing = true
        prefs.installing = what
        InferenceService.start(app)
    }

    fun installEnded() {
        installing = false
        prefs.installing = null
        val m = _model.value
        if (m is ModelState.Loaded || m is ModelState.Loading) InferenceService.start(app) else InferenceService.stop(app)
    }

    /** What the last process was installing when it died, once. */
    fun takeInterruptedInstall(): String? {
        if (installing) return null
        return prefs.installing?.also { prefs.installing = null }
    }

    private fun startFailure(t: Throwable): String {
        val free = app.filesDir.usableSpace
        return if (free < LOW_STORAGE_BYTES) "The phone is almost out of storage: ${free / 1_000_000} MB free. Free up space, then try again."
        else t.message ?: t.toString()
    }

    fun start() {
        scope.launch {
            try {
                val encoders = installEncoders(app)
                System.loadLibrary("onnxruntime")
                val arm = Build.SUPPORTED_ABIS.firstOrNull() == "arm64-v8a"
                val engine = CommonplaceEngine(
                    EngineOptions(
                        dataDir = app.filesDir.absolutePath,
                        encodersDir = encoders.absolutePath,
                        leafModel = "onnx/model_quantized.onnx",
                        ettinModel = if (arm) "onnx/model_sel8.onnx" else "onnx/model_quint8_avx2.onnx",
                        encoderThreads = 4u,
                        llmThreads = prefs.threads.toUInt(),
                        pinBigCores = arm,
                        keepQueryText = BuildConfig.DEBUG,
                    ),
                )
                prefs.applyTo(engine)
                _state.value = EngineState.Ready(engine)
                refreshLibrary()
                if (onScreen()) ensureModel(null)
            } catch (t: Throwable) {
                Log.e(TAG, "engine start failed", t)
                _state.value = EngineState.Failed(startFailure(t))
            }
        }
    }

    fun refreshLibrary() {
        val e = engineOrNull ?: return
        _library.value = e.library()
    }

    /** Make sure the wanted model is loaded. `kind == null` means the default (fast, else small). */
    suspend fun ensureModel(kind: ModelKind?) = withContext(Dispatchers.IO) {
        modelLock.withLock {
            val e = engineOrNull ?: return@withLock
            val lib = e.library()
            _library.value = lib
            val gguf = lib.models.filter { !it.filePath.endsWith(".litertlm") }.map { it.kind }
            val litePack = lib.models.firstOrNull { it.filePath.endsWith(".litertlm") }
            if (loadingMarker.exists()) {
                if (lastExitBlamesModel()) prefs.crashedModel = loadingMarker.readText()
                loadingMarker.delete()
            }
            val crashed = prefs.crashedModel
            // With no llama.cpp model installed, an old choice of the CPU must not leave the Gemma pack unused.
            val useTpu = (prefs.backend == Backend.Tpu || gguf.isEmpty()) && litePack != null && !liteFailed && crashed != litePack.filePath
            val usable = lib.models.filter { !it.filePath.endsWith(".litertlm") && it.filePath != crashed }.map { it.kind }
            // `null` means the everyday model: the user's choice, else fast, else small. A low-RAM phone loads the
            // fast model only when the user picks it: Android kills the app when it holds about 5.5 GB.
            val want = kind ?: listOfNotNull(prefs.modelKind, ModelKind.FAST.takeIf { !prefs.lowRam }, ModelKind.SMALL).firstOrNull { it in usable }
            val current = _model.value
            if (current is ModelState.Loaded && (if (useTpu) current.backend == Backend.Tpu else current.backend == Backend.Cpu && current.kind == want)) {
                return@withLock
            }
            if (!useTpu && (want == null || want !in gguf)) {
                liteRt?.let {
                    e.setForeignLlm(null, null)
                    it.close()
                    liteRt = null
                }
                _model.value = notLoadedReason(lib, crashed)?.let { ModelState.Failed(it) } ?: ModelState.None
                return@withLock
            }
            _model.value = ModelState.Loading(want)
            InferenceService.start(app)
            val poll = pollLoadProgress(e, useTpu)
            try {
                if (useTpu) {
                    e.unloadModel()
                    val lr = liteRt ?: LiteRtBackend(app, litePack!!.filePath, prefs.speculative).also { liteRt = it }
                    markLoading(litePack.filePath)
                    try {
                        lr.initialize()
                    } finally {
                        loadingMarker.delete()
                    }
                    e.setForeignLlm(lr, litePack.filePath)
                    _model.value = ModelState.Loaded(litePack!!.kind, lr.id(), Backend.Tpu)
                } else {
                    liteRt?.close()
                    liteRt = null
                    markLoading(lib.models.first { it.kind == want && !it.filePath.endsWith(".litertlm") }.filePath)
                    val st = try {
                        e.loadModel(want!!)
                    } finally {
                        loadingMarker.delete()
                    }
                    _model.value = if (st.loaded) ModelState.Loaded(st.kind, st.id, Backend.Cpu) else ModelState.None
                }
            } catch (t: Throwable) {
                Log.e(TAG, "model load failed", t)
                if (useTpu) {
                    liteRt?.close()
                    liteRt = null
                    liteFailed = true
                    if (want != null && want in gguf) {
                        val st = runCatching { e.loadModel(want) }.getOrNull()
                        if (st != null && st.loaded) {
                            _model.value = ModelState.Loaded(st.kind, st.id, Backend.Cpu)
                            return@withLock
                        }
                    }
                }
                _model.value = ModelState.Failed(t.message ?: t.toString())
            } finally {
                // Joined, so a last write of the poll cannot land after this clear and leave the rule stuck.
                withContext(NonCancellable) { poll.cancelAndJoin() }
                _loadProgress.value = null
            }
        }
    }

    /** A full disk must not stop a load that needs no disk; the marker is only a safety net. */
    private fun markLoading(path: String) {
        runCatching { loadingMarker.writeText(path) }
    }

    /** A user or an update can also end the process during a load. Only a crash or low memory counts against the model. */
    private fun lastExitBlamesModel(): Boolean {
        val am = app.getSystemService(ActivityManager::class.java)
        val reason = am.getHistoricalProcessExitReasons(null, 0, 1).firstOrNull()?.reason ?: return true
        return reason in setOf(
            ApplicationExitInfo.REASON_CRASH,
            ApplicationExitInfo.REASON_CRASH_NATIVE,
            ApplicationExitInfo.REASON_LOW_MEMORY,
            ApplicationExitInfo.REASON_SIGNALED,
            ApplicationExitInfo.REASON_ANR,
            ApplicationExitInfo.REASON_INITIALIZATION_FAILURE,
            ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE,
        )
    }

    /** Why an installed model is not loading, and where the user can change that; null when none is installed. */
    private fun notLoadedReason(lib: LibraryInfo, crashed: String?): String? {
        val models = lib.models.filter { it.answers }
        val failed = models.firstOrNull { it.filePath == crashed }
        val lite = models.firstOrNull { it.filePath.endsWith(".litertlm") }
        return when {
            failed != null -> "${failed.title} stopped the app while it loaded. Choose it in Settings to try again."
            liteFailed && lite != null -> "${lite.title} could not start on this phone."
            prefs.lowRam && models.isNotEmpty() && models.all { it.kind == ModelKind.FAST } -> "${models.first().title} needs more memory than this phone has. Choose it in Settings to try anyway."
            else -> null
        }
    }

    /**
     * Weights give a real fraction, up to [WEIGHTS_SHARE] of the bar. The prompt state after them, and all of a LiteRT
     * load, report nothing: that part eases towards the end, paced by how long it took on this phone last time.
     */
    private fun pollLoadProgress(e: CommonplaceEngine, lite: Boolean): Job = scope.launch {
        var pacedFrom = if (lite) System.currentTimeMillis() else 0L
        val expected = prefs.loadTailMs(lite).toDouble()
        var weights = 0f
        try {
            while (isActive) {
                if (!lite) weights = e.modelLoadProgress()
                if (pacedFrom == 0L && weights >= 1f) pacedFrom = System.currentTimeMillis()
                val paced = if (pacedFrom == 0L) 0f else (1 - exp(-2.3 * (System.currentTimeMillis() - pacedFrom) / expected)).toFloat()
                _loadProgress.value = when {
                    lite -> LoadProgress(paced, null)
                    pacedFrom == 0L -> LoadProgress(weights * WEIGHTS_SHARE, (weights * 100).toInt())
                    else -> LoadProgress(WEIGHTS_SHARE + (1 - WEIGHTS_SHARE) * paced, null)
                }
                delay(100)
            }
        } finally {
            if (pacedFrom != 0L) prefs.noteLoadTail(lite, System.currentTimeMillis() - pacedFrom)
        }
    }

    /** An answer runs: its model stays loaded when the app goes off screen, so coming back does not load a second copy. */
    @Volatile
    var answering = false

    private fun onScreen() = ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)

    /** A process that starts hidden does not load the model, so the app comes on screen with none. */
    fun loadModelOnScreen() {
        scope.launch { ensureModel(null) }
    }

    /**
     * Android 17 limits a hidden app to 3 GiB, so the kernel swaps about 2 GB of Ling's 5 GB out and other apps get killed.
     * Models below the limit stay loaded. The lock also catches a load that was running when the app went off screen.
     */
    fun unloadModelOverHiddenLimit() {
        scope.launch {
            modelLock.withLock {
                val m = _model.value
                if (!answering && !onScreen() && m is ModelState.Loaded && m.backend == Backend.Cpu && m.kind == ModelKind.FAST) {
                    engineOrNull?.unloadModel()
                    _model.value = ModelState.None
                }
            }
        }
    }

    /** The answer has ended, on screen or off it. */
    fun answerEnded() {
        answering = false
        if (unloadAfterAnswer && !onScreen()) {
            unloadAfterAnswer = false
            unloadModel()
        } else {
            unloadAfterAnswer = false
            unloadModelOverHiddenLimit()
        }
    }

    /** Stop the running answer. LiteRT reads the core's flag only when a token arrives, so its prefill needs a direct cancel. */
    fun cancelAnswer() {
        engineOrNull?.cancel()
        liteRt?.cancelRunning()
    }

    @Volatile private var unloadAfterAnswer = false

    /** Free the model under memory pressure; retrieval keeps working. An answer keeps its model until it ends. */
    fun unloadModel() {
        if (answering) {
            unloadAfterAnswer = true
            return
        }
        scope.launch {
            modelLock.withLock {
                engineOrNull?.unloadModel()
                liteRt?.close()
                liteRt = null
                if (_model.value is ModelState.Loaded) _model.value = ModelState.None
            }
        }
    }

    fun reloadModel() {
        // A running answer holds its model until it ends; stop it so two models are never resident.
        cancelAnswer()
        scope.launch {
            modelLock.withLock {
                engineOrNull?.let {
                    it.unloadModel()
                    it.setForeignLlm(null, null)
                }
                // A new LiteRT engine picks up changed settings, such as speculative decoding.
                liteRt?.close()
                liteRt = null
                liteFailed = false
                _model.value = ModelState.None
            }
            ensureModel(null)
        }
    }

    /** Push [prefs] to the engine. Off the main thread: the thread count waits for a running answer. */
    fun applyPrefs() {
        scope.launch { engineOrNull?.let { prefs.applyTo(it) } }
    }

    fun thermalHeadroom(): Float? {
        val pm = app.getSystemService(Context.POWER_SERVICE) as PowerManager
        val h = pm.getThermalHeadroom(10)
        return if (h.isNaN()) null else h
    }

    companion object {
        private const val TAG = "Commonplace"
        private const val WEIGHTS_SHARE = 0.85f
        private const val LOW_STORAGE_BYTES = 500_000_000L

        /** Copy the bundled ONNX encoders out of the APK once per app version. */
        private fun installEncoders(app: Application): File {
            val dir = File(app.filesDir, "encoders")
            val stamp = File(dir, ".version")
            val version = "${BuildConfig.VERSION_CODE}-${app.packageManager.getPackageInfo(app.packageName, 0).lastUpdateTime}"
            if (stamp.exists() && stamp.readText() == version) return dir
            dir.deleteRecursively()
            val files = app.assets.open("encoders/manifest.txt").bufferedReader().readLines().filter { it.isNotBlank() }
            for (f in files) {
                val rel = f.removePrefix("./")
                if (rel == "manifest.txt") continue
                val out = File(dir, rel)
                out.parentFile?.mkdirs()
                app.assets.open("encoders/$rel").use { input -> out.outputStream().use { input.copyTo(it, 1 shl 20) } }
            }
            stamp.writeText(version)
            return dir
        }
    }
}
