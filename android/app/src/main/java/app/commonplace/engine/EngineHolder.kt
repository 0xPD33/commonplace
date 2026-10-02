package app.commonplace.engine

import android.app.Application
import android.content.Context
import android.os.Build
import android.os.PowerManager
import android.util.Log
import app.commonplace.BuildConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
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
import java.io.File

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

/** Owns the Rust engine for the whole process. All engine calls run off the main thread. */
class EngineHolder(private val app: Application) {
    /** Process-wide work that must outlive a screen, such as an import that has to refresh the library. */
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val modelLock = Mutex()
    val prefs = Prefs(app)

    private val _state = MutableStateFlow<EngineState>(EngineState.Starting)
    val state: StateFlow<EngineState> = _state.asStateFlow()

    private val _library = MutableStateFlow<LibraryInfo?>(null)
    val library: StateFlow<LibraryInfo?> = _library.asStateFlow()

    private val _model = MutableStateFlow<ModelState>(ModelState.None)
    val model: StateFlow<ModelState> = _model.asStateFlow()

    private var liteRt: LiteRtBackend? = null

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
                        ettinModel = if (arm) "onnx/model_nb8.onnx" else "onnx/model_quint8_avx2.onnx",
                        encoderThreads = 4u,
                        llmThreads = prefs.threads.toUInt(),
                        pinBigCores = arm,
                    ),
                )
                prefs.applyTo(engine)
                _state.value = EngineState.Ready(engine)
                refreshLibrary()
                ensureModel(null)
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

    /**
     * Make sure the wanted model is loaded. `kind == null` means the default (fast, else small).
     * Deep mode swaps the fast model out because both do not fit in RAM together.
     */
    suspend fun ensureModel(kind: ModelKind?) = withContext(Dispatchers.IO) {
        modelLock.withLock {
            val e = engineOrNull ?: return@withLock
            val lib = e.library()
            _library.value = lib
            val gguf = lib.models.filter { !it.filePath.endsWith(".litertlm") }.map { it.kind }
            val litePack = lib.models.firstOrNull { it.filePath.endsWith(".litertlm") }
            val useTpu = prefs.backend == Backend.Tpu && kind != ModelKind.DEEP && litePack != null
            // `null` means the everyday model: the user's choice, else fast, else small.
            val want = kind ?: listOf(prefs.modelKind, ModelKind.FAST, ModelKind.SMALL).firstOrNull { it in gguf }
            val current = _model.value
            if (current is ModelState.Loaded && (if (useTpu) current.backend == Backend.Tpu else current.backend == Backend.Cpu && current.kind == want)) {
                return@withLock
            }
            if (!useTpu && (want == null || want !in gguf)) {
                liteRt?.let {
                    e.setForeignLlm(null)
                    it.close()
                    liteRt = null
                }
                _model.value = ModelState.None
                return@withLock
            }
            _model.value = ModelState.Loading(want)
            InferenceService.start(app)
            try {
                if (useTpu) {
                    e.unloadModel()
                    val lr = liteRt ?: LiteRtBackend(app, litePack!!.filePath).also { liteRt = it }
                    lr.initialize()
                    e.setForeignLlm(lr)
                    _model.value = ModelState.Loaded(litePack!!.kind, lr.id(), Backend.Tpu)
                } else {
                    liteRt?.close()
                    liteRt = null
                    val st = e.loadModel(want!!)
                    _model.value = if (st.loaded) ModelState.Loaded(st.kind, st.id, Backend.Cpu) else ModelState.None
                }
            } catch (t: Throwable) {
                Log.e(TAG, "model load failed", t)
                _model.value = ModelState.Failed(t.message ?: t.toString())
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
                engineOrNull?.unloadModel()
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
