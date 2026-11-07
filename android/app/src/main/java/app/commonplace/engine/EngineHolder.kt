package app.commonplace.engine

import android.app.Application
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

    private var liteRt: LiteRtBackend? = null
    /** The Gemma pack failed to start (another chip, or no NPU access): answer with llama.cpp until the next reload. */
    private var liteFailed = false
    /** Exists while LiteRT loads a model. A native crash there kills the process, so the next start finds it. */
    private val liteLoading = File(app.filesDir, "litert-loading")

    val engineOrNull: CommonplaceEngine?
        get() = (state.value as? EngineState.Ready)?.engine

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
                    ),
                )
                prefs.applyTo(engine)
                _state.value = EngineState.Ready(engine)
                refreshLibrary()
                if (onScreen()) ensureModel(null)
            } catch (t: Throwable) {
                Log.e(TAG, "engine start failed", t)
                _state.value = EngineState.Failed(t.message ?: t.toString())
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
            if (liteLoading.exists()) {
                prefs.liteCrashed = liteLoading.readText()
                liteLoading.delete()
            }
            val useTpu = prefs.backend == Backend.Tpu && litePack != null && !liteFailed && prefs.liteCrashed != litePack.filePath
            // `null` means the everyday model: the user's choice, else fast, else small. A low-RAM phone loads the
            // fast model only when the user picks it: Android kills the app when it holds about 5.5 GB.
            val want = kind ?: listOfNotNull(prefs.modelKind, ModelKind.FAST.takeIf { !prefs.lowRam }, ModelKind.SMALL).firstOrNull { it in gguf }
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
                _model.value = ModelState.None
                return@withLock
            }
            _model.value = ModelState.Loading(want)
            InferenceService.start(app)
            val poll = pollLoadProgress(e, useTpu)
            try {
                if (useTpu) {
                    e.unloadModel()
                    val lr = liteRt ?: LiteRtBackend(app, litePack!!.filePath, prefs.speculative).also { liteRt = it }
                    liteLoading.writeText(litePack.filePath)
                    try {
                        lr.initialize()
                    } finally {
                        liteLoading.delete()
                    }
                    e.setForeignLlm(lr, litePack.filePath)
                    _model.value = ModelState.Loaded(litePack!!.kind, lr.id(), Backend.Tpu)
                } else {
                    liteRt?.close()
                    liteRt = null
                    val st = e.loadModel(want!!)
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

    /** Free the model under memory pressure; retrieval keeps working. */
    fun unloadModel() {
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
        engineOrNull?.cancel()
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
