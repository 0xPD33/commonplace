package app.commonplace.ui

import android.app.UiModeManager
import android.content.Intent
import android.os.Build
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.IosShare
import androidx.compose.material.icons.outlined.Speed
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.foundation.selection.selectable
import androidx.compose.ui.semantics.Role
import uniffi.commonplace_ffi.ModelKind
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import app.commonplace.CommonplaceApp
import app.commonplace.engine.Backend
import app.commonplace.engine.ModelState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uniffi.commonplace_ffi.AskInput
import uniffi.commonplace_ffi.AskListener
import uniffi.commonplace_ffi.AnswerCard
import uniffi.commonplace_ffi.AnswerSegment
import uniffi.commonplace_ffi.AskStage
import uniffi.commonplace_ffi.DeviceInfo
import uniffi.commonplace_ffi.QuerySummary
import uniffi.commonplace_ffi.SourceItem
import java.io.File
import java.text.DateFormat
import java.util.Date
import java.util.Locale

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit, onDiagnostics: () -> Unit) {
    val holder = (LocalContext.current.applicationContext as CommonplaceApp).engine
    val prefs = holder.prefs
    val lib by holder.library.collectAsState()
    var modelKind by remember { mutableStateOf(prefs.modelKind) }
    val model by holder.model.collectAsState()
    var backend by remember { mutableStateOf(prefs.backend) }
    var threads by remember { mutableIntStateOf(prefs.threads) }
    var evidence by remember { mutableIntStateOf(prefs.evidenceTokens) }
    var answer by remember { mutableIntStateOf(prefs.answerTokens) }
    var thinkBudget by remember { mutableIntStateOf(prefs.thinkBudget) }
    var planner by remember { mutableStateOf(prefs.usePlanner) }
    var rewrite by remember { mutableStateOf(prefs.rewrite) }
    var timings by remember { mutableStateOf(prefs.showTimings) }
    var nightMode by remember { mutableIntStateOf(prefs.nightMode) }

    fun apply() {
        prefs.threads = threads
        prefs.evidenceTokens = evidence
        prefs.answerTokens = answer
        prefs.thinkBudget = thinkBudget
        prefs.usePlanner = planner
        prefs.rewrite = rewrite
        prefs.showTimings = timings
        holder.applyPrefs()
    }
    // Save on every way out: the back arrow, the system back gesture, Diagnostics, and rotation.
    DisposableEffect(Unit) { onDispose { apply() } }

    Scaffold(topBar = {
        TopAppBar(title = { Text("Settings") }, navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } })
    }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(20.dp).testTag("settings")) {
            SectionLabel("Appearance")
            Spacer(Modifier.height(8.dp))
            val uiMode = LocalContext.current.getSystemService(UiModeManager::class.java)
            val modes = listOf(UiModeManager.MODE_NIGHT_AUTO to "System", UiModeManager.MODE_NIGHT_NO to "Light", UiModeManager.MODE_NIGHT_YES to "Dark")
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().testTag("theme_choice")) {
                modes.forEachIndexed { i, (mode, label) ->
                    SegmentedButton(
                        selected = nightMode == mode,
                        onClick = {
                            nightMode = mode
                            prefs.nightMode = mode
                            // Android persists this per app and recreates the activity in the new mode.
                            uiMode.setApplicationNightMode(mode)
                        },
                        shape = SegmentedButtonDefaults.itemShape(i, modes.size),
                    ) { Text(label) }
                }
            }

            Spacer(Modifier.height(20.dp))
            SectionLabel("Model")
            Spacer(Modifier.height(8.dp))
            Text(
                when (val m = model) {
                    is ModelState.Loaded -> m.id
                    is ModelState.Loading -> "Loading…"
                    is ModelState.Failed -> "Failed: ${m.message}"
                    ModelState.None -> "No model loaded"
                },
                style = MaterialTheme.typography.bodyMedium,
            )
            val choices = lib?.models?.filter { it.kind != ModelKind.DEEP && !it.filePath.endsWith(".litertlm") } ?: emptyList()
            if (choices.size > 1) {
                Spacer(Modifier.height(8.dp))
                for (m in choices) {
                    Row(
                        Modifier.fillMaxWidth().selectable(selected = modelKind == m.kind, role = Role.RadioButton) {
                            modelKind = m.kind
                            prefs.modelKind = m.kind
                            holder.reloadModel()
                        }.padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        RadioButton(selected = modelKind == m.kind, onClick = null)
                        Spacer(Modifier.width(10.dp))
                        Column {
                            Text(m.title, style = MaterialTheme.typography.bodyLarge)
                            Text(if (m.kind == ModelKind.FAST) "Better answers" else "Faster, less memory", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Text("Engine", style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(6.dp))
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                Backend.entries.forEachIndexed { i, b ->
                    SegmentedButton(
                        selected = backend == b,
                        onClick = {
                            backend = b
                            prefs.backend = b
                            holder.reloadModel()
                        },
                        shape = SegmentedButtonDefaults.itemShape(i, Backend.entries.size),
                    ) { Text(if (b == Backend.Cpu) "CPU · llama.cpp" else "TPU · LiteRT-LM") }
                }
            }
            Text(
                "TPU needs a LiteRT model pack (.litertlm). Without one, the CPU engine is used.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )

            Spacer(Modifier.height(20.dp))
            LabeledSlider("CPU threads", threads.toFloat(), 2f..6f, 3, "$threads") { threads = it.toInt() }
            Text("4 is cooler, 6 is faster until the phone gets hot.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)

            Spacer(Modifier.height(20.dp))
            SectionLabel("Answers")
            SwitchRow("Show timings under answers", timings) { timings = it }
            SwitchRow("Understand the question first (fixes typos and follow-ups, a few seconds)", rewrite) { rewrite = it }
            SwitchRow("Plan complex questions", planner) { planner = it }
            LabeledSlider("Answer length (tokens)", answer.toFloat(), 150f..800f, 12, "$answer") { answer = (it / 50).toInt() * 50 }
            LabeledSlider("Thinking budget (tokens)", thinkBudget.toFloat(), 128f..1536f, 10, "$thinkBudget") { thinkBudget = (it / 128).toInt() * 128 }
            Text(
                "When thinking is on (the brain button next to the question box), the model reasons for at most this many tokens before it answers. A warm phone writes about 15 tokens a second.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(Modifier.height(20.dp))
            SectionLabel("Advanced")
            LabeledSlider("Evidence budget (tokens)", evidence.toFloat(), 300f..2500f, 21, "$evidence") { evidence = (it / 100).toInt() * 100 }
            Text(
                "More evidence can help long answers but takes longer to read on a phone CPU.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(24.dp))
            HorizontalDivider()
            TextButton(onClick = onDiagnostics, modifier = Modifier.testTag("open_diagnostics")) { Text("Diagnostics and benchmark") }
        }
    }
}

@Composable
private fun SwitchRow(label: String, value: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        Switch(checked = value, onCheckedChange = onChange)
    }
}

@Composable
private fun LabeledSlider(label: String, value: Float, range: ClosedFloatingPointRange<Float>, steps: Int, shown: String, onChange: (Float) -> Unit) {
    Column(Modifier.padding(vertical = 4.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
            Text(shown, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.primary)
        }
        // Tick marks crowd the track at 10+ steps; the value label already shows the step.
        Slider(
            value = value,
            onValueChange = onChange,
            valueRange = range,
            steps = steps,
            colors = SliderDefaults.colors(activeTickColor = Color.Transparent, inactiveTickColor = Color.Transparent),
        )
    }
}

private val BENCH = listOf(
    "What is the capital of Australia?",
    "Why do we have seasons?",
    "Compare lions and tigers",
    "How does a refrigerator work?",
    "When did the Berlin Wall fall and why?",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DiagnosticsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val holder = (ctx.applicationContext as CommonplaceApp).engine
    val scope = rememberCoroutineScope()
    var device by remember { mutableStateOf<DeviceInfo?>(null) }
    var recent by remember { mutableStateOf<List<QuerySummary>>(emptyList()) }
    var bench by remember { mutableStateOf<String?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    // The benchmark shares the engine with the Ask screen: stop it when the user leaves.
    DisposableEffect(Unit) { onDispose { if (bench?.startsWith("Running") == true) holder.engineOrNull?.cancel() } }
    LaunchedEffect(refresh) {
        withContext(Dispatchers.IO) {
            val e = holder.engineOrNull ?: return@withContext
            device = e.deviceInfo()
            recent = e.recentQueries()
        }
    }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Diagnostics") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
            actions = {
                IconButton(onClick = {
                    val path = holder.engineOrNull?.telemetryPath() ?: return@IconButton
                    val f = File(path)
                    if (!f.exists()) return@IconButton
                    val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", f)
                    ctx.startActivity(
                        Intent.createChooser(
                            Intent(Intent.ACTION_SEND).setType("application/json").putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                            "Export query timings",
                        ),
                    )
                }) { Icon(Icons.Outlined.IosShare, "Export timings") }
            },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad).testTag("diagnostics"),
            contentPadding = PaddingValues(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerLow, border = hairline(), modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        SectionLabel("Device")
                        Spacer(Modifier.height(6.dp))
                        Text("${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} · ${Build.SUPPORTED_ABIS.firstOrNull()}", style = MaterialTheme.typography.bodyMedium)
                        device?.let { d ->
                            Text("Build ${d.build} · big cores ${d.bigCores.joinToString(",")}", style = MaterialTheme.typography.bodySmall)
                            Text(d.llamaSystemInfo.trim(), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            if (!d.cpuFeaturesOk) Text(d.cpuFeaturesError, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.error)
                        }
                        holder.thermalHeadroom()?.let { Text(String.format(Locale.ROOT, "Thermal headroom %.2f", it), style = MaterialTheme.typography.bodySmall) }
                    }
                }
            }
            item {
                FilledTonalButton(
                    enabled = bench == null || !bench!!.startsWith("Running"),
                    onClick = {
                        scope.launch(Dispatchers.IO) {
                            val e = holder.engineOrNull ?: return@launch
                            holder.ensureModel(null)
                            for ((i, q) in BENCH.withIndex()) {
                                if (!isActive) return@launch
                                bench = "Running ${i + 1} of ${BENCH.size}: $q"
                                runCatching { e.ask(AskInput(q, emptyList(), false, false, holder.thermalHeadroom()), SilentListener) }
                            }
                            val rs = e.recentQueries().take(BENCH.size)
                            fun p50(xs: List<Double>) = xs.sorted().getOrNull(xs.size / 2) ?: 0.0
                            bench = "Benchmark: card p50 ${seconds(p50(rs.map { it.timing.cardMs }))} · first word p50 ${seconds(p50(rs.mapNotNull { it.timing.ttftMs }))} · " +
                                String.format(Locale.ROOT, "%.1f tok/s", p50(rs.mapNotNull { it.timing.decodeTps }))
                            refresh++
                        }
                    },
                    modifier = Modifier.fillMaxWidth().testTag("run_benchmark"),
                ) {
                    Icon(Icons.Outlined.Speed, null)
                    Spacer(Modifier.width(8.dp))
                    Text("Run benchmark (5 questions)")
                }
                bench?.let { Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp).testTag("benchmark_result")) }
            }
            item { SectionLabel("Last ${recent.size} questions") }
            items(recent) { q -> QueryRow(q) }
        }
    }
}

@Composable
private fun QueryRow(q: QuerySummary) {
    val t = q.timing
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainer, border = hairline(), modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp)) {
            Text(q.query, style = MaterialTheme.typography.titleSmall)
            Text(DateFormat.getTimeInstance(DateFormat.SHORT).format(Date(q.tsMs.toLong())) + " · " + q.model.ifBlank { "search only" }, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
            Spacer(Modifier.height(6.dp))
            val parts = buildList {
                add("card ${seconds(t.cardMs)}")
                t.ttftMs?.let { add("first word ${seconds(it)}") }
                add("total ${seconds(t.totalMs)}")
                t.prefillTps?.let { add(String.format(Locale.ROOT, "prefill %.0f tok/s", it)) }
                t.decodeTps?.let { add(String.format(Locale.ROOT, "decode %.1f tok/s", it)) }
                add("${t.evidenceTokens} evidence tokens")
                add(String.format(Locale.ROOT, "RSS %.0f MB", t.peakRssMb))
                q.thermalHeadroom?.let { add(String.format(Locale.ROOT, "thermal %.2f", it)) }
                q.threads?.let { add("$it threads") }
            }
            Text(parts.joinToString(" · "), style = MaterialTheme.typography.bodySmall)
            q.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
        }
    }
}

private object SilentListener : AskListener {
    override fun onStage(stage: AskStage, detail: String) {}
    override fun onCard(card: AnswerCard) {}
    override fun onSources(sources: List<SourceItem>) {}
    override fun onThinking(text: String, done: Boolean) {}
    override fun onAnswer(segments: List<AnswerSegment>, done: Boolean) {}
}
