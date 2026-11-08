package app.commonplace.ui

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.BatteryManager
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.IosShare
import androidx.compose.material.icons.outlined.Speed
import androidx.compose.material.icons.outlined.TaskAlt
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import app.commonplace.BuildConfig
import app.commonplace.CommonplaceApp
import app.commonplace.engine.Backend
import app.commonplace.engine.ModelState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uniffi.commonplace_ffi.AnswerCard
import uniffi.commonplace_ffi.AnswerSegment
import uniffi.commonplace_ffi.AskInput
import uniffi.commonplace_ffi.AskListener
import uniffi.commonplace_ffi.AskStage
import uniffi.commonplace_ffi.CommonplaceEngine
import uniffi.commonplace_ffi.DeviceInfo
import uniffi.commonplace_ffi.LibraryInfo
import uniffi.commonplace_ffi.QuerySummary
import uniffi.commonplace_ffi.SourceItem
import java.io.File
import java.text.DateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs
import org.json.JSONArray
import org.json.JSONObject

private val BENCH = listOf(
    "What is the capital of Australia?",
    "Why do we have seasons?",
    "Compare lions and tigers",
    "How does a refrigerator work?",
    "When did the Berlin Wall fall and why?",
)

/** Our speed rule: a run that starts above this battery temperature is slower than a cool phone. */
private const val WARM_C = 34f

private const val ISSUE_URL = "https://github.com/0xPD33/commonplace/issues/new"

/** `scripts/build-android.sh` copies `eval/canary.jsonl` here, so the app and the eval check the same questions. */
private const val CANARY_ASSET = "eval/canary.jsonl"

private enum class Check { Speed, Accuracy }

private data class Battery(val celsius: Float?, val plugged: Boolean) {
    override fun toString() =
        (celsius?.let { String.format(Locale.ROOT, "battery %.1f °C", it) } ?: "battery temperature unknown") +
            if (plugged) ", plugged in" else ", on battery"
}

/** The 5 fixed questions only: the user's own questions never enter a report. */
private data class SpeedResult(
    val model: String,
    val library: String,
    val start: Battery,
    val end: Battery,
    val headroom: Float?,
    val rows: List<QuerySummary>,
) {
    private fun median(xs: List<Double>) = xs.sorted().let { if (it.isEmpty()) null else it[it.size / 2] }
    val card get() = median(rows.map { it.timing.cardMs })
    val firstWord get() = median(rows.mapNotNull { it.timing.ttftMs })
    val total get() = median(rows.map { it.timing.totalMs })
    val prefill get() = median(rows.mapNotNull { it.timing.prefillTps })
    val decode get() = median(rows.mapNotNull { it.timing.decodeTps })
    val peakMb get() = rows.maxOfOrNull { it.timing.peakRssMb }
    val warm get() = (start.celsius ?: 0f) > WARM_C

    fun summary() = listOfNotNull(
        firstWord?.let { "first word ${seconds(it)}" },
        total?.takeIf { firstWord != null }?.let { "full answer ${seconds(it)}" },
        decode?.let { String.format(Locale.ROOT, "%.1f tok/s", it) },
    ).joinToString(" · ").ifEmpty { "search only, card ${card?.let(::seconds)}" }

    fun stats() = listOfNotNull(
        firstWord?.let { "First word" to seconds(it) },
        total?.takeIf { firstWord != null }?.let { "Full answer" to seconds(it) },
        card?.let { "Search card" to seconds(it) },
        prefill?.let { "Reading" to String.format(Locale.ROOT, "%.0f tok/s", it) },
        decode?.let { "Writing" to String.format(Locale.ROOT, "%.1f tok/s", it) },
        peakMb?.let { "Peak memory" to String.format(Locale.ROOT, "%,.0f MB", it) },
    )

    fun text() = buildString {
        appendLine("Speed: $model; library $library")
        appendLine("Start: $start" + (headroom?.let { String.format(Locale.ROOT, ", thermal headroom %.2f", it) } ?: "") + if (warm) " (warm phone: slower)" else "")
        appendLine("End: $end")
        appendLine("Median of ${rows.size} questions: " + stats().joinToString(" · ") { (k, v) -> "$k $v" })
        appendLine("Per question: card / first word / full answer / reading / writing")
        rows.forEachIndexed { i, q ->
            val t = q.timing
            append("${i + 1}. ${q.query}: ${seconds(t.cardMs)} / ${t.ttftMs?.let(::seconds) ?: "-"} / ${seconds(t.totalMs)} / ")
            append(t.prefillTps?.let { String.format(Locale.ROOT, "%.0f", it) } ?: "-")
            append(" / ")
            appendLine(t.decodeTps?.let { String.format(Locale.ROOT, "%.1f", it) } ?: "-")
            q.error?.let { appendLine("   error: $it") }
        }
    }.trimEnd()
}

/** One canary question. [passed] counts a pass on the second try; [firstTry] tells sampling noise from a real failure. */
private data class CanaryItem(val id: String, val query: String, val expected: String, val firstTry: Boolean, val passed: Boolean, val answer: String)

private data class AccuracyResult(val model: String, val library: String, val items: List<CanaryItem>) {
    val passed get() = items.count { it.passed }
    val retried get() = items.count { it.passed && !it.firstTry }
    val problems get() = items.filter { !it.passed || !it.firstTry }

    fun summary() = "$passed of ${items.size} passed" + if (retried > 0) ", $retried only on the second try" else ""

    fun text() = buildString {
        appendLine("Accuracy: $model; library $library")
        appendLine("Questions with known answers: ${summary()}")
        for (c in problems) {
            appendLine("${c.id} ${if (c.passed) "passed on the second try" else "failed"}: ${c.query} Expected: ${c.expected}. Answer: ${c.answer}")
        }
    }.trimEnd()
}

private fun reportText(app: String, ramGb: Int, speed: SpeedResult?, accuracy: AccuracyResult?) = buildString {
    appendLine("Commonplace device report")
    appendLine("Phone: ${phoneName()} (${chipName()})")
    appendLine("Android: ${androidLine()}")
    appendLine("RAM: $ramGb GB")
    appendLine("App: $app")
    speed?.let { appendLine(); appendLine(it.text()) }
    accuracy?.let { appendLine(); appendLine(it.text()) }
}.trimEnd()

/** GitHub fills an issue form from query parameters named after its field ids (.github/ISSUE_TEMPLATE/device-report.yml). */
private fun githubUri(app: String, ramGb: Int, speed: SpeedResult?, accuracy: AccuracyResult?): Uri = Uri.parse(ISSUE_URL).buildUpon()
    .appendQueryParameter("template", "device-report.yml")
    .appendQueryParameter("title", "Device report: ${phoneName()}")
    .appendQueryParameter("phone", "${phoneName()} (${chipName()}) · Android ${androidLine()}")
    .appendQueryParameter("ram", "$ramGb GB")
    .appendQueryParameter("app", app)
    .apply { speed?.let { appendQueryParameter("bench", it.summary()) } }
    .apply { accuracy?.let { appendQueryParameter("accuracy", it.summary()) } }
    .appendQueryParameter("report", reportText(app, ramGb, speed, accuracy))
    .build()

// The same rule as `expect_ok` in eval/report.py. Change both together.
private val SENTENCE_END = Regex("(?<=[.!?])\\s+|\\n+")
private val NUMBER = Regex("(?<![\\w.])-?\\d[\\d,]*(?:\\.\\d+)?(?:\\s*(thousand|million|billion|trillion))?", RegexOption.IGNORE_CASE)
private val SCALE = mapOf("thousand" to 1e3, "million" to 1e6, "billion" to 1e9, "trillion" to 1e12)

private fun numbers(text: String) = NUMBER.findAll(text).mapNotNull { m ->
    m.value.split(Regex("\\s+"))[0].replace(",", "").toDoubleOrNull()?.times(SCALE[m.groupValues[1].lowercase()] ?: 1.0)
}

private fun expectOk(answer: String, expect: Any): Boolean {
    if (expect is JSONObject && expect.has("number")) {
        val n = expect.get("number")
        val xs = if (n is JSONArray) List(n.length()) { n.getDouble(it) } else listOf(expect.getDouble("number"))
        val tol = expect.optDouble("tol", 0.02)
        return numbers(answer).any { v -> xs.any { x -> abs(v - x) <= abs(x) * tol } }
    }
    val (texts, k) = if (expect is JSONObject) expect.getJSONArray("text") to expect.getInt("sentences") else (expect as JSONArray) to 1
    val head = answer.split(SENTENCE_END).filter { it.isNotBlank() }.take(k).joinToString(" ").lowercase()
    return (0 until texts.length()).any { texts.getString(it).lowercase() in head }
}

private fun expectedText(expect: Any): String {
    if (expect is JSONObject && expect.has("number")) return expect.get("number").toString()
    val a = if (expect is JSONObject) expect.getJSONArray("text") else expect as JSONArray
    return List(a.length()) { a.getString(it) }.joinToString(" or ")
}

private class Canary(val id: String, val query: String, val expect: Any)

private fun loadCanary(ctx: Context): List<Canary>? = runCatching {
    ctx.assets.open(CANARY_ASSET).bufferedReader().readLines().filter { it.isNotBlank() }.map {
        val o = JSONObject(it)
        Canary(o.getString("id"), o.getString("query"), o.get("expect"))
    }
}.getOrNull()

/** Kept for the life of the process, so results are still there to share after the user leaves the screen. */
private var lastSpeed: SpeedResult? = null
private var lastAccuracy: AccuracyResult? = null

private fun phoneName() = "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}"

private fun chipName() = listOf(Build.SOC_MANUFACTURER, Build.SOC_MODEL).filter { it.isNotBlank() && it != Build.UNKNOWN }.joinToString(" ").ifEmpty { "unknown chip" }

private fun androidLine() = "${Build.VERSION.RELEASE}, ${Build.SUPPORTED_ABIS.firstOrNull()}"

private fun battery(ctx: Context): Battery {
    // A sticky system broadcast: a null receiver reads the last value and registers nothing.
    val i = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
    val tenths = i?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, Int.MIN_VALUE) ?: Int.MIN_VALUE
    return Battery(if (tenths == Int.MIN_VALUE) null else tenths / 10f, (i?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0)
}

/** Android reports the RAM left after the kernel's share, so 12 GB shows as about 11 GiB. Round up to a sold size. */
private fun ramGb(ctx: Context): Int {
    val mi = ActivityManager.MemoryInfo().also { (ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(it) }
    val gib = mi.totalMem / 1073741824.0
    return listOf(2, 3, 4, 6, 8, 10, 12, 16, 18, 24, 32).firstOrNull { it >= gib } ?: kotlin.math.ceil(gib).toInt()
}

private fun modelName(m: ModelState, lib: LibraryInfo?): String = when (m) {
    is ModelState.Loaded -> {
        val npu = m.backend == Backend.Tpu
        val pack = lib?.models?.firstOrNull { it.filePath.endsWith(".litertlm") == npu && (npu || it.kind == m.kind) }
        (pack?.title ?: m.id) + if (npu) " on the NPU" else " on the CPU"
    }
    else -> "none (search only)"
}

private fun libraryLine(lib: LibraryInfo?): String {
    lib ?: return "unknown"
    val wikidata = if (!lib.hasWikidata) "" else if (lib.wikidataEnabled) ", Wikidata on" else ", Wikidata off"
    return "${lib.packs.count { it.enabled }} of ${lib.packs.size} packs on$wikidata, ${bytes(lib.totalBytes.toLong())} in total"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DiagnosticsScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val holder = (ctx.applicationContext as CommonplaceApp).engine
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    val model by holder.model.collectAsState()
    val lib by holder.library.collectAsState()
    val ram = remember { ramGb(ctx) }
    val canary = remember { loadCanary(ctx) }
    var device by remember { mutableStateOf<DeviceInfo?>(null) }
    var recent by remember { mutableStateOf<List<QuerySummary>>(emptyList()) }
    var refresh by remember { mutableIntStateOf(0) }
    var speed by remember { mutableStateOf(lastSpeed) }
    var accuracy by remember { mutableStateOf(lastAccuracy) }
    var job by remember { mutableStateOf<Job?>(null) }
    var running by remember { mutableStateOf<Check?>(null) }
    var stage by remember { mutableStateOf<String?>(null) }
    var progress by remember { mutableStateOf(0f) }
    var notice by remember { mutableStateOf<String?>(null) }
    var lastRun by remember { mutableStateOf<Check?>(null) }
    var copied by remember { mutableStateOf(false) }
    var now by remember { mutableStateOf(battery(ctx)) }
    var showDetails by rememberSaveable { mutableStateOf(false) }
    var showRecent by rememberSaveable { mutableStateOf(false) }
    val app = "${BuildConfig.VERSION_NAME} (${device?.build ?: "build unknown"})"
    val list = rememberLazyListState()
    // A run takes minutes; a screen timeout would stop the app and with it the run.
    val view = LocalView.current
    DisposableEffect(running) {
        view.keepScreenOn = running != null
        onDispose { view.keepScreenOn = false }
    }
    // A finished run puts the report on screen: it sits below the fold on most phones.
    LaunchedEffect(speed, accuracy, running) { if ((speed != null || accuracy != null) && running == null) list.animateScrollToItem(3) }

    fun stop(why: String?) {
        if (running == null) return
        holder.engineOrNull?.cancel()
        job?.cancel()
        stage = null
        notice = why
    }
    // The checks share the engine with the Ask screen, and a hidden app runs slower: stop when the user leaves.
    DisposableEffect(Unit) { onDispose { if (job?.isActive == true) holder.engineOrNull?.cancel() } }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) { stop("Stopped: the app left the screen. Keep it open while a check runs.") }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { now = battery(ctx) }
    LaunchedEffect(refresh) {
        withContext(Dispatchers.IO) {
            val e = holder.engineOrNull ?: return@withContext
            device = e.deviceInfo()
            recent = e.recentQueries()
        }
    }

    /** One fresh question, as the Ask screen would run it, with the model kept loaded while it runs. */
    fun ask(e: CommonplaceEngine, q: String) = try {
        holder.answering = true
        runCatching { e.ask(AskInput(q, emptyList(), false, holder.thermalHeadroom(), emptyList()), SilentListener) }.getOrNull()
    } finally {
        holder.answering = false
        holder.unloadModelOverHiddenLimit()
    }

    fun start(check: Check, body: suspend CoroutineScope.(CommonplaceEngine) -> Unit) {
        notice = null
        copied = false
        lastRun = check
        running = check
        job = scope.launch(Dispatchers.IO) {
            try {
                val e = holder.engineOrNull ?: return@launch
                progress = 0f
                stage = "Loading the model…"
                holder.ensureModel(null)
                body(e)
                refresh++
            } finally {
                running = null
                stage = null
            }
        }
    }

    fun runSpeed() = start(Check.Speed) { e ->
        val start = battery(ctx)
        val headroom = holder.thermalHeadroom()
        val rows = mutableListOf<QuerySummary>()
        for ((i, q) in BENCH.withIndex()) {
            if (!isActive) return@start
            progress = (i + 0.5f) / BENCH.size
            stage = "Question ${i + 1} of ${BENCH.size}: $q"
            ask(e, q)
            // Newest first; a cancelled ask may leave no record.
            e.recentQueries().firstOrNull()?.takeIf { it.query == q }?.let(rows::add)
        }
        if (!isActive) return@start
        if (rows.isEmpty()) {
            notice = "The speed test got no answers. Check that the library has packs switched on."
            return@start
        }
        val r = SpeedResult(modelName(holder.model.value, holder.library.value), libraryLine(holder.library.value), start, battery(ctx), headroom, rows)
        lastSpeed = r
        speed = r
        now = r.end
    }

    fun runAccuracy() = start(Check.Accuracy) { e ->
        val qs = canary ?: return@start
        if (holder.model.value !is ModelState.Loaded) {
            notice = "The accuracy check needs an answer model. Install one in the Library."
            return@start
        }
        val items = mutableListOf<CanaryItem>()
        for ((i, c) in qs.withIndex()) {
            if (!isActive) return@start
            progress = (i + 0.5f) / qs.size
            stage = "Question ${i + 1} of ${qs.size}: ${c.query}"
            val first = ask(e, c.query)?.answerText.orEmpty()
            val firstOk = expectOk(first, c.expect)
            // Answers are sampled, so one miss can be chance: ask once more before it counts as a failure.
            val second = if (firstOk || !isActive) null else {
                stage = "Asking again: ${c.query}"
                ask(e, c.query)?.answerText.orEmpty()
            }
            val shown = (second ?: first).replace(Regex("\\s+"), " ").take(160)
            items += CanaryItem(c.id, c.query, expectedText(c.expect), firstOk, firstOk || (second != null && expectOk(second, c.expect)), shown)
        }
        if (!isActive) return@start
        val r = AccuracyResult(modelName(holder.model.value, holder.library.value), libraryLine(holder.library.value), items)
        lastAccuracy = r
        accuracy = r
    }

    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Diagnostics") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
        )
    }) { pad ->
        LazyColumn(
            Modifier.fillMaxSize().padding(pad).testTag("diagnostics"),
            state = list,
            contentPadding = PaddingValues(20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                Paper {
                    SectionLabel("This phone")
                    Spacer(Modifier.height(8.dp))
                    SelectionContainer {
                        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Fact("Phone", phoneName())
                            Fact("Chip", chipName())
                            Fact("Android", "${Build.VERSION.RELEASE} · ${Build.SUPPORTED_ABIS.firstOrNull()}")
                            Fact("RAM", "$ram GB")
                            Fact("App", "${BuildConfig.VERSION_NAME}" + (device?.build?.let { " · $it" } ?: ""))
                            Fact("Model", modelName(model, lib))
                            Fact("Library", libraryLine(lib))
                            Fact("Battery", now.toString().removePrefix("battery "))
                        }
                    }
                    device?.takeIf { !it.cpuFeaturesOk }?.let {
                        Spacer(Modifier.height(6.dp))
                        Text(it.cpuFeaturesError, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                    }
                    TextButton(onClick = { showDetails = !showDetails }, contentPadding = PaddingValues(0.dp), modifier = Modifier.testTag("device_details")) {
                        Text(if (showDetails) "Hide technical details" else "Show technical details")
                    }
                    if (showDetails) {
                        SelectionContainer {
                            Column {
                                device?.let { d ->
                                    Text("Big cores ${d.bigCores.joinToString(",")}", style = MaterialTheme.typography.bodySmall)
                                    Text(d.llamaSystemInfo.trim(), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                }
                                holder.thermalHeadroom()?.let { Text(String.format(Locale.ROOT, "Thermal headroom %.2f (1.0 = throttling)", it), style = MaterialTheme.typography.bodySmall) }
                            }
                        }
                    }
                }
            }
            item {
                CheckCard(
                    title = "Speed",
                    about = "Five fixed questions measure how fast this phone answers. It takes about a minute, longer on a slower phone. Keep the app open.",
                    warning = if (running == null && (now.celsius ?: 0f) > WARM_C) {
                        String.format(Locale.ROOT, "The battery is at %.1f °C. A warm phone answers slower: let it cool below %.0f °C for a fair result.", now.celsius, WARM_C)
                    } else null,
                    button = if (speed == null) "Run speed test" else "Run speed test again",
                    icon = Icons.Outlined.Speed,
                    tag = "run_benchmark",
                    active = running == Check.Speed,
                    enabled = running == null,
                    stage = stage,
                    progress = progress,
                    note = notice.takeIf { lastRun == Check.Speed },
                    onRun = ::runSpeed,
                    onStop = { stop("Stopped.") },
                )
            }
            item {
                CheckCard(
                    title = "Accuracy",
                    about = if (canary == null) "This build has no accuracy questions." else
                        "${canary.size} questions with known answers, such as the capital of Australia. A wrong answer points to a problem with this phone, the model or the library. It takes about a minute, longer on a slower phone.",
                    warning = null,
                    button = if (accuracy == null) "Run accuracy check" else "Run accuracy check again",
                    icon = Icons.Outlined.TaskAlt,
                    tag = "run_accuracy",
                    active = running == Check.Accuracy,
                    enabled = running == null && canary != null,
                    stage = stage,
                    progress = progress,
                    note = notice.takeIf { lastRun == Check.Accuracy },
                    onRun = ::runAccuracy,
                    onStop = { stop("Stopped.") },
                )
            }
            if (speed != null || accuracy != null) item {
                Paper(Modifier.testTag("benchmark_result")) {
                    SectionLabel("Report")
                    speed?.let { r ->
                        Spacer(Modifier.height(10.dp))
                        Text("Speed", style = MaterialTheme.typography.titleSmall)
                        Text(
                            "Median of ${r.rows.size} questions · ${r.model}" + if (r.warm) " · warm phone" else "",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.height(6.dp))
                        SelectionContainer {
                            Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                for ((k, v) in r.stats()) Fact(k, v)
                            }
                        }
                    }
                    accuracy?.let { r ->
                        Spacer(Modifier.height(14.dp))
                        Text("Accuracy", style = MaterialTheme.typography.titleSmall)
                        Text(r.model, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Spacer(Modifier.height(6.dp))
                        Text(
                            r.summary(),
                            style = MaterialTheme.typography.bodyLarge,
                            color = if (r.passed == r.items.size) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.error,
                            modifier = Modifier.testTag("accuracy_summary"),
                        )
                        SelectionContainer {
                            Column(verticalArrangement = Arrangement.spacedBy(6.dp), modifier = Modifier.padding(top = 6.dp)) {
                                for (c in r.problems) {
                                    Column {
                                        Text(c.query, style = MaterialTheme.typography.bodyMedium)
                                        Text(
                                            (if (c.passed) "Right on the second try. " else "Expected ${c.expected}. ") + "Answer: ${c.answer}",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                    }
                                }
                            }
                        }
                    }
                    Spacer(Modifier.height(16.dp))
                    Button(
                        onClick = { runCatching { ctx.startActivity(Intent(Intent.ACTION_VIEW, githubUri(app, ram, speed, accuracy))) } },
                        modifier = Modifier.fillMaxWidth().testTag("github_report"),
                    ) { Text("Report on GitHub") }
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = {
                                val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, reportText(app, ram, speed, accuracy))
                                ctx.startActivity(Intent.createChooser(send, null))
                            },
                            modifier = Modifier.weight(1f).testTag("share_report"),
                        ) {
                            Icon(Icons.Outlined.IosShare, null)
                            Spacer(Modifier.width(8.dp))
                            Text("Share")
                        }
                        OutlinedButton(
                            onClick = {
                                clipboard.setText(AnnotatedString(reportText(app, ram, speed, accuracy)))
                                copied = true
                            },
                            modifier = Modifier.weight(1f).testTag("copy_report"),
                        ) {
                            Icon(Icons.Outlined.ContentCopy, null)
                            Spacer(Modifier.width(8.dp))
                            Text("Copy")
                        }
                    }
                    if (copied) Text("Copied the report.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp))
                    Spacer(Modifier.height(8.dp))
                    Text(
                        "The report holds this phone, the app and the results of these checks. It has none of your own questions. " +
                            "Report on GitHub opens your browser; Commonplace itself stays offline.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (recent.isNotEmpty()) item {
                Spacer(Modifier.height(8.dp))
                SectionLabel("Your questions")
                Text(
                    (if (recent.size == 1) "Timings of your last question." else "Timings of your last ${recent.size} questions.") +
                        " They show what you asked, so keep them hidden before a screenshot.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                TextButton(onClick = { showRecent = !showRecent }, contentPadding = PaddingValues(0.dp), modifier = Modifier.testTag("toggle_recent")) {
                    Text(if (showRecent) "Hide your questions" else "Show your questions")
                }
            }
            if (showRecent) items(recent) { q -> QueryRow(q) }
            item {
                val log = holder.engineOrNull?.telemetryPath()?.let(::File)?.takeIf { it.exists() }
                if (log != null) {
                    TextButton(
                        onClick = {
                            val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.files", log)
                            ctx.startActivity(
                                Intent.createChooser(
                                    Intent(Intent.ACTION_SEND).setType("application/json").putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
                                    "Export full log",
                                ),
                            )
                        },
                        contentPadding = PaddingValues(0.dp),
                        modifier = Modifier.testTag("export_log"),
                    ) { Text("Export full log") }
                    Text(
                        "The full log holds every question you asked. Share it only with someone you trust.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

@Composable
private fun CheckCard(
    title: String,
    about: String,
    warning: String?,
    button: String,
    icon: ImageVector,
    tag: String,
    active: Boolean,
    enabled: Boolean,
    stage: String?,
    progress: Float,
    note: String?,
    onRun: () -> Unit,
    onStop: () -> Unit,
) {
    Paper {
        SectionLabel(title)
        Spacer(Modifier.height(8.dp))
        Text(about, style = MaterialTheme.typography.bodyMedium)
        warning?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, modifier = Modifier.testTag("warm_warning"))
        }
        Spacer(Modifier.height(12.dp))
        if (active) {
            InkRule(progress)
            Spacer(Modifier.height(8.dp))
            stage?.let { Text(it, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.testTag("benchmark_stage")) }
            ModelLoadRule(Modifier.padding(top = 6.dp))
            Spacer(Modifier.height(8.dp))
            OutlinedButton(onClick = onStop, modifier = Modifier.fillMaxWidth().testTag("stop_benchmark")) { Text("Stop") }
        } else {
            FilledTonalButton(onClick = onRun, enabled = enabled, modifier = Modifier.fillMaxWidth().testTag(tag)) {
                Icon(icon, null)
                Spacer(Modifier.width(8.dp))
                Text(button)
            }
        }
        note?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 8.dp)) }
    }
}

@Composable
private fun Paper(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerLow, border = hairline(), modifier = modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) { content() }
    }
}

@Composable
private fun Fact(label: String, value: String) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.width(104.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum"), modifier = Modifier.weight(1f))
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
