package app.commonplace.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.gestures.animateScrollBy
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.AddComment
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.History
import androidx.compose.material.icons.outlined.KeyboardArrowDown
import androidx.compose.material.icons.outlined.LocalLibrary
import androidx.compose.material.icons.outlined.Mic
import androidx.compose.material.icons.outlined.MicOff
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconToggleButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.InputChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.viewmodel.compose.viewModel
import app.commonplace.CommonplaceApp
import app.commonplace.engine.Backend
import app.commonplace.engine.EngineState
import app.commonplace.engine.ModelState
import app.commonplace.engine.loadCatalog
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import uniffi.commonplace_ffi.LibraryInfo
import uniffi.commonplace_ffi.ModelKind

private val EXAMPLES = listOf(
    "How does a refrigerator work?",
    "Compare the Nile and the Amazon",
    "How do vaccines work?",
    "When did the Berlin Wall fall and why?",
    "Population density of Malta",
    "26 miles in km",
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AskScreen(
    onOpen: (OpenSource) -> Unit,
    onLibrary: () -> Unit,
    onSettings: () -> Unit,
    onDiagnostics: () -> Unit,
    onHistory: () -> Unit,
    vm: AskViewModel = viewModel(),
) {
    val app = LocalContext.current.applicationContext as CommonplaceApp
    val holder = app.engine
    val engineState by holder.state.collectAsState()
    val library by holder.library.collectAsState()
    val model by holder.model.collectAsState()
    var showOffline by rememberSaveable { mutableStateOf(false) }
    var menu by remember { mutableStateOf(false) }
    val scroll = TopAppBarDefaults.pinnedScrollBehavior()
    val inputFocus = remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    val focusManager = LocalFocusManager.current
    val canThinkHarder = library?.models?.any { it.kind == ModelKind.DEEP } == true
    val micPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.toggleVoice() else vm.micDenied()
    }
    val context = LocalContext.current
    val onMic = {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (granted || vm.listening) vm.toggleVoice() else micPermission.launch(Manifest.permission.RECORD_AUDIO)
    }
    // A removed or switched-off document cannot be searched: drop the scope instead of answering from nothing.
    val scopeGone = vm.scope?.let { s -> library?.packs?.none { it.packId == s.packId && it.enabled } == true } == true
    LaunchedEffect(scopeGone) { if (scopeGone) vm.clearScope() }
    // The mic must not outlive the screen, in the background or behind a source page.
    DisposableEffect(Unit) { onDispose { vm.stopVoice() } }
    LifecycleEventEffect(Lifecycle.Event.ON_STOP) { vm.stopVoice() }

    Scaffold(
        modifier = Modifier.nestedScroll(scroll.nestedScrollConnection),
        topBar = {
            TopAppBar(
                scrollBehavior = scroll,
                colors = TopAppBarDefaults.topAppBarColors(scrolledContainerColor = MaterialTheme.colorScheme.surfaceContainer),
                title = {
                    Column {
                        Text("Commonplace", style = MaterialTheme.typography.titleLarge, fontStyle = FontStyle.Italic)
                        ModelStatusLine(model, installed = library?.models?.any { it.kind != ModelKind.VOICE } == true, known = library != null)
                    }
                },
                actions = {
                    if (vm.turns.isNotEmpty()) {
                        IconButton(onClick = vm::newTopic, enabled = !vm.busy, modifier = Modifier.testTag("new_topic")) {
                            Icon(Icons.Outlined.AddComment, "New topic")
                        }
                    }
                    IconButton(onClick = onHistory, enabled = !vm.busy, modifier = Modifier.testTag("open_history")) { Icon(Icons.Outlined.History, "History") }
                    IconButton(onClick = onLibrary, modifier = Modifier.testTag("open_library")) { Icon(Icons.Outlined.LocalLibrary, "Library") }
                    Box {
                        IconButton(onClick = { menu = true }, modifier = Modifier.testTag("overflow")) { Icon(Icons.Outlined.MoreVert, "More") }
                        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                            DropdownMenuItem(text = { Text("Settings") }, onClick = { menu = false; onSettings() }, modifier = Modifier.testTag("menu_settings"))
                            DropdownMenuItem(text = { Text("Diagnostics") }, onClick = { menu = false; onDiagnostics() }, modifier = Modifier.testTag("menu_diagnostics"))
                        }
                    }
                },
            )
        },
        bottomBar = {
            if (engineState is EngineState.Ready && library?.packs?.isNotEmpty() == true) {
                AskBar(
                    value = vm.input,
                    onValue = { vm.input = it },
                    busy = vm.busy,
                    onSend = {
                        keyboard?.hide()
                        focusManager.clearFocus()
                        vm.ask()
                    },
                    onStop = vm::stop,
                    thinking = vm.thinking,
                    onToggleThink = vm::toggleThinking,
                    listening = vm.listening,
                    voiceError = vm.voiceError,
                    onMic = if (library != null && vm.voiceAvailable) onMic else null, // `library` changes when a pack is installed or removed
                    focus = inputFocus,
                    scope = vm.scope,
                    onClearScope = vm::clearScope,
                )
            }
        },
    ) { pad ->
        when (val s = engineState) {
            EngineState.Starting -> Opening(pad)
            is EngineState.Failed -> Centered(pad) {
                ErrorNote("The engine could not start: ${s.message}")
            }
            is EngineState.Ready -> {
                val lib = library
                if (lib == null || lib.packs.isEmpty()) {
                    EmptyLibrary(pad, onLibrary, onOffline = { showOffline = true })
                } else if (vm.turns.isEmpty()) {
                    Welcome(pad, lib, holder.prefs.recent, onAsk = { vm.ask(it) }, onOffline = { showOffline = true })
                } else {
                    Conversation(pad, vm, holder.prefs.showTimings, canThinkHarder, onOpen, onDraft = { d ->
                        vm.input = d
                        inputFocus.requestFocus()
                    })
                }
            }
        }
    }
    if (showOffline) OfflineSheet(onDismiss = { showOffline = false })
}

@Composable
private fun ModelStatusLine(model: ModelState, installed: Boolean, known: Boolean) {
    val (text, color) = when {
        // Until the library is open, the app cannot tell whether a model is installed.
        !known -> "" to MaterialTheme.colorScheme.onSurfaceVariant
        else -> modelStatus(model, installed)
    }
    Text(text, style = MaterialTheme.typography.labelSmall, color = color, modifier = Modifier.testTag("model_status"))
}

@Composable
private fun modelStatus(model: ModelState, installed: Boolean): Pair<String, Color> {
    return when (model) {
        // Android unloads the model in the background; the next question loads it again.
        ModelState.None -> (if (installed) "Model loads on your next question" else "Search only · no model installed") to MaterialTheme.colorScheme.onSurfaceVariant
        is ModelState.Loading -> "Loading model…" to MaterialTheme.colorScheme.secondary
        is ModelState.Loaded -> (if (model.kind == ModelKind.DEEP) "Deep model" else "Ready") +
            (if (model.backend == Backend.Cpu) "" else " · ${model.backend.name.uppercase()}") to MaterialTheme.colorScheme.primary
        is ModelState.Failed -> "Model failed to load" to MaterialTheme.colorScheme.error
    }
}

@Composable
private fun Centered(pad: PaddingValues, content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(pad).padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) { content() }
}

@Composable
private fun Welcome(pad: PaddingValues, lib: LibraryInfo, recent: List<String>, onAsk: (String) -> Unit, onOffline: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 16.dp).testTag("welcome"),
    ) {
        WelcomeHead()
        Spacer(Modifier.height(16.dp))
        OfflineBadge(onOffline, Modifier.riseIn("welcome_badge", delayMs = 140))
        Spacer(Modifier.height(20.dp))
        val passages = lib.packs.sumOf { it.passages.toLong() }.toULong()
        val names = lib.packs.joinToString(", ") { it.title }
        Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerLow, border = hairline(), modifier = Modifier.fillMaxWidth().riseIn("welcome_library", delayMs = 210)) {
            Column(Modifier.padding(16.dp)) {
                SectionLabel("Your library")
                Spacer(Modifier.height(6.dp))
                Text(names, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(
                    "${count(passages)} passages · snapshot ${lib.snapshotDate}${if (lib.hasWikidata) " · Wikidata facts" else ""}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (recent.isNotEmpty()) {
            Spacer(Modifier.height(24.dp))
            Column(Modifier.riseIn("welcome_recent", delayMs = 280)) {
                SectionLabel("Recent")
                Spacer(Modifier.height(4.dp))
                QuestionRows(recent.take(5), Icons.Outlined.History, onAsk)
            }
        }
        Spacer(Modifier.height(24.dp))
        Column(Modifier.riseIn("welcome_examples", delayMs = 350)) {
            SectionLabel("Try asking")
            Spacer(Modifier.height(4.dp))
            // Recent shows 5 questions and there are 6 examples, so at least one example is always left.
            QuestionRows(EXAMPLES - recent.take(5).toSet(), null, onAsk, Modifier.testTag("examples"))
        }
    }
}

/** Questions as rows with rules between them, like the entries of an index. */
@Composable
private fun QuestionRows(questions: List<String>, icon: ImageVector?, onAsk: (String) -> Unit, modifier: Modifier = Modifier) {
    Column(modifier) {
        for (q in questions) {
            ClickableRow(onClick = { onAsk(q) }) {
                Row(Modifier.padding(vertical = 13.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (icon != null) {
                        Icon(icon, null, tint = MaterialTheme.colorScheme.outline, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(12.dp))
                    }
                    Text(q, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        }
    }
}

/** The heading of the welcome page. It also shows while the library opens, so it rises in only once. */
@Composable
private fun WelcomeHead() {
    Spacer(Modifier.height(24.dp))
    Text("What would you\nlike to know?", style = MaterialTheme.typography.displaySmall, modifier = Modifier.riseIn("welcome_title"))
    Spacer(Modifier.height(12.dp))
    Text(
        "Answers come from the library on this phone, with sources you can open and check.",
        style = MaterialTheme.typography.bodyLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.riseIn("welcome_lede", delayMs = 70),
    )
}

/** The first seconds of a cold start: the welcome heading at once, and a note while the packs open. */
@Composable
private fun Opening(pad: PaddingValues) {
    Column(Modifier.fillMaxSize().padding(pad).padding(horizontal = 24.dp, vertical = 16.dp).testTag("opening")) {
        WelcomeHead()
        Spacer(Modifier.height(28.dp))
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.riseIn("opening_note", delayMs = 140)) {
            CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(10.dp))
            Text("Opening your library…", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun EmptyLibrary(pad: PaddingValues, onLibrary: () -> Unit, onOffline: () -> Unit) {
    val ctx = LocalContext.current
    Column(
        Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(24.dp).testTag("empty_library"),
        horizontalAlignment = Alignment.Start,
    ) {
        Spacer(Modifier.height(24.dp))
        Text("Add your first library", style = MaterialTheme.typography.displaySmall)
        Spacer(Modifier.height(12.dp))
        Text(
            "Commonplace answers from knowledge packs stored on this phone. It never goes online, so you add packs yourself.",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(16.dp))
        OfflineBadge(onOffline)
        Spacer(Modifier.height(24.dp))
        // The starter bundle is one file. A catalog without bundles lists the starter packs instead.
        val starters = remember { loadCatalog(ctx).let { c -> c.bundles.filter { it.recommended }.ifEmpty { c.packs.filter { it.recommended } } } }
        val oneFile = starters.size == 1
        if (starters.isNotEmpty()) {
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerLow, border = hairline(), modifier = Modifier.fillMaxWidth().testTag("get_started")) {
                Column(Modifier.padding(16.dp)) {
                    SectionLabel("Get started")
                    Spacer(Modifier.height(8.dp))
                    Text(
                        if (oneFile) "Download one file in your browser: ${bytes(starters[0].downloadBytes)}."
                        else "Download the starter packs in your browser: ${bytes(starters.sumOf { it.downloadBytes })} in total.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    Spacer(Modifier.height(8.dp))
                    for (p in starters) {
                        Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                            Column(Modifier.weight(1f)) {
                                Text(p.title, style = MaterialTheme.typography.titleSmall)
                                if (oneFile) Text(p.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                            Text(bytes(p.downloadBytes), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
        }
        if (oneFile) {
            Step(1, "Download the ${starters[0].title}", "Open Library, tap ${starters[0].title} under Get more, and download the file in your browser. It lands in Downloads.")
            Step(2, "Install it here", "Tap Install from Downloads in Library and select the file. Commonplace checks every byte before it uses it.")
        } else {
            Step(1, "Download the starter packs", "Open Library, tap each pack under Get more, and download it in your browser. The files land in Downloads.")
            Step(2, "Install them here", "Tap Install from Downloads in Library and select all the files. Commonplace checks every byte before it uses them.")
        }
        Step(3, "Ask anything", "Search works at once. Add a model pack for written answers with citations.")
        Spacer(Modifier.height(24.dp))
        Button(onClick = onLibrary, modifier = Modifier.fillMaxWidth().height(52.dp).testTag("go_import")) { Text("Open Library") }
    }
}

@Composable
private fun Step(n: Int, title: String, body: String) {
    Row(Modifier.padding(vertical = 10.dp)) {
        NumberDisc(n.toUInt(), Modifier.padding(top = 2.dp))
        Spacer(Modifier.width(14.dp))
        Column {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(body, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Conversation(
    pad: PaddingValues,
    vm: AskViewModel,
    showTimings: Boolean,
    canThinkHarder: Boolean,
    onOpen: (OpenSource) -> Unit,
    onDraft: (String) -> Unit,
) {
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    // Follow the streaming answer while the user stays at the bottom; a scroll up pauses it.
    var follow by remember { mutableStateOf(true) }
    LaunchedEffect(list) {
        snapshotFlow { list.isScrollInProgress }.collect { scrolling -> if (!scrolling) follow = !list.canScrollForward }
    }
    LaunchedEffect(vm.turns.lastOrNull()?.id) {
        if (vm.turns.isNotEmpty()) {
            follow = true
            list.animateScrollToItem(vm.turns.size - 1)
        }
    }
    LaunchedEffect(Unit) {
        snapshotFlow { vm.turns.lastOrNull()?.let { listOf(it.segments, it.sources.size, it.done, it.card, it.thought.length) } }.collect {
            if (vm.turns.isNotEmpty() && follow && !list.isScrollInProgress) list.scrollToItem(vm.turns.size - 1, Int.MAX_VALUE / 2)
        }
    }
    val haptic = LocalHapticFeedback.current
    val last = vm.turns.lastOrNull()
    var wasAnswering by remember { mutableStateOf(false) }
    LaunchedEffect(last?.id, last?.answering) {
        if (last == null) return@LaunchedEffect
        val finished = wasAnswering && !last.answering
        wasAnswering = last.answering
        if (!finished || last.stopped) return@LaunchedEffect
        haptic.performHapticFeedback(if (last.error != null) HapticFeedbackType.Reject else HapticFeedbackType.Confirm)
        // The copy, share and follow-up row appears after the last token; wait for it to grow in, then show it.
        delay(450)
        val info = list.layoutInfo
        val end = info.visibleItemsInfo.lastOrNull()?.takeIf { it.index == vm.turns.lastIndex } ?: return@LaunchedEffect
        val hidden = end.offset + end.size - (info.viewportEndOffset - info.afterContentPadding)
        if (follow && hidden > 0) list.animateScrollBy(hidden.toFloat())
    }
    Box(Modifier.fillMaxSize().padding(pad)) {
        LazyColumn(
            state = list,
            modifier = Modifier.fillMaxSize().testTag("conversation"),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(28.dp),
        ) {
            itemsIndexed(vm.turns, key = { _, t -> t.id }) { i, t ->
                Column {
                    if (i > 0) Ornament(Modifier.padding(bottom = 28.dp))
                    val latest = i == vm.turns.lastIndex && !vm.busy
                    TurnView(
                        t, showTimings, canThinkHarder, onOpen,
                        onThinkHarder = { vm.thinkHarder(t) },
                        onThinkThrough = if (latest) ({ vm.thinkThrough(t) }) else null,
                        onRetry = if (latest) ({ vm.retry(t) }) else null,
                        // A reformat rewrites the latest answer, so only the latest answer offers one.
                        onAsk = if (latest) ({ q -> vm.ask(q) }) else null,
                        onDraft = if (latest) onDraft else null,
                    )
                }
            }
        }
        AnimatedVisibility(
            visible = list.canScrollForward && !follow,
            enter = fadeIn() + scaleIn(),
            exit = fadeOut() + scaleOut(),
            modifier = Modifier.align(Alignment.BottomEnd).padding(end = 16.dp, bottom = 16.dp),
        ) {
            SmallFloatingActionButton(
                onClick = {
                    follow = true
                    scope.launch { list.scrollToItem(vm.turns.lastIndex.coerceAtLeast(0), Int.MAX_VALUE / 2) }
                },
                containerColor = MaterialTheme.colorScheme.surfaceContainerHighest,
                modifier = Modifier.testTag("scroll_bottom"),
            ) { Icon(Icons.Outlined.KeyboardArrowDown, "Scroll to the end") }
        }
    }
}

@Composable
private fun AskBar(
    value: String,
    onValue: (String) -> Unit,
    busy: Boolean,
    onSend: () -> Unit,
    onStop: () -> Unit,
    thinking: Boolean,
    onToggleThink: () -> Unit,
    listening: Boolean,
    voiceError: String?,
    onMic: (() -> Unit)?,
    focus: FocusRequester,
    scope: AskScope?,
    onClearScope: () -> Unit,
) {
    val haptic = LocalHapticFeedback.current
    // A short note after each switch of Think says what it changes, then goes away.
    var hint by remember { mutableStateOf("") }
    var hintCount by remember { mutableIntStateOf(0) }
    var showHint by remember { mutableStateOf(false) }
    LaunchedEffect(hintCount) {
        if (hintCount == 0) return@LaunchedEffect
        showHint = true
        delay(3500)
        showHint = false
    }
    Surface(color = MaterialTheme.colorScheme.surface) { Column {
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        AnimatedVisibility(visible = showHint, enter = expandVertically() + fadeIn(), exit = shrinkVertically() + fadeOut()) {
            Text(
                hint,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 20.dp, end = 20.dp, top = 10.dp).testTag("think_hint"),
            )
        }
        if (scope != null) {
            InputChip(
                selected = true,
                onClick = onClearScope,
                label = { Text("Searching: ${scope.title}", maxLines = 1, overflow = TextOverflow.Ellipsis) },
                trailingIcon = { Icon(Icons.Outlined.Close, "Remove, search the whole library", Modifier.size(InputChipDefaults.AvatarSize)) },
                modifier = Modifier.padding(start = 12.dp, end = 12.dp, top = 8.dp).testTag("scope_chip"),
            )
        }
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // A draft set from outside (a chip, the voice transcript) puts the cursor after it.
            var field by remember { mutableStateOf(TextFieldValue(value, TextRange(value.length))) }
            if (field.text != value) field = TextFieldValue(value, TextRange(value.length))
            TextField(
                value = field,
                onValueChange = {
                    field = it
                    onValue(it.text)
                },
                placeholder = {
                    Text(
                        when {
                            listening -> "Listening…"
                            voiceError != null -> "Voice input failed: $voiceError"
                            else -> "Ask a question…"
                        },
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                // Think and the mic live inside the box, so the bar has one height: the field's and the send button's.
                leadingIcon = {
                    ThinkToggle(thinking) {
                        haptic.performHapticFeedback(if (it) HapticFeedbackType.ToggleOn else HapticFeedbackType.ToggleOff)
                        hint = if (it) "Think is on: the model reasons before it answers. Slower, often better for why and how questions." else "Think is off: quick answers."
                        hintCount++
                        onToggleThink()
                    }
                },
                trailingIcon = if (onMic != null && !busy) {
                    {
                        IconToggleButton(checked = listening, onCheckedChange = { onMic() }, modifier = Modifier.testTag("mic_toggle")) {
                            Icon(
                                if (listening) Icons.Outlined.MicOff else Icons.Outlined.Mic,
                                if (listening) "Stop listening" else "Speak your question",
                                tint = if (listening) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                            )
                        }
                    }
                } else null,
                readOnly = listening, // the transcript rewrites the field until the user taps stop
                modifier = Modifier.weight(1f).focusRequester(focus).testTag("ask_input"),
                shape = RoundedCornerShape(28.dp),
                maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { onSend() }),
                colors = TextFieldDefaults.colors(
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                    disabledIndicatorColor = Color.Transparent,
                    focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                    unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                ),
            )
            Spacer(Modifier.width(8.dp))
            // 60 dp: the one-line field, a 28 sp line plus 16 dp above and below.
            if (busy) {
                FilledIconButton(onClick = onStop, modifier = Modifier.size(60.dp).testTag("ask_stop")) { Icon(Icons.Outlined.Stop, "Stop") }
            } else {
                FilledIconButton(onClick = onSend, enabled = value.isNotBlank() && !listening, modifier = Modifier.size(60.dp).testTag("ask_send")) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowForward, "Ask")
                }
            }
        }
    } }
}

/** The Think switch in the question box: a filled disc while on. On, the model reasons before each answer. */
@Composable
private fun ThinkToggle(on: Boolean, onToggle: (Boolean) -> Unit) {
    val c = MaterialTheme.colorScheme
    val bg by animateColorAsState(if (on) c.primaryContainer else Color.Transparent, tween(220), label = "think_bg")
    val fg by animateColorAsState(if (on) c.onPrimaryContainer else c.onSurfaceVariant, tween(220), label = "think_fg")
    Surface(
        checked = on,
        onCheckedChange = onToggle,
        shape = CircleShape,
        color = bg,
        contentColor = fg,
        modifier = Modifier.padding(start = 8.dp).size(40.dp).semantics { stateDescription = if (on) "On" else "Off" }.testTag("think_toggle"),
    ) {
        Box(contentAlignment = Alignment.Center) { Icon(Icons.Outlined.Psychology, "Think first") }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun OfflineSheet(onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("offline_sheet")) {
        Column(Modifier.verticalScroll(rememberScrollState()).navigationBarsPadding().padding(horizontal = 24.dp).padding(bottom = 24.dp)) {
            Text("Private by construction", style = MaterialTheme.typography.headlineSmall)
            Spacer(Modifier.height(12.dp))
            for (line in listOf(
                "Commonplace does not have the Internet permission. Android itself blocks it from opening any network connection.",
                "Your questions, the library and the language model all stay on this phone.",
                "It does not use Google Play Services, accounts or analytics.",
                "You can check this in the app's system settings: the Permissions list has no network access.",
            )) {
                Row(Modifier.padding(vertical = 6.dp)) {
                    Text("•", style = MaterialTheme.typography.bodyLarge, modifier = Modifier.width(18.dp))
                    Text(line, style = MaterialTheme.typography.bodyLarge)
                }
            }
            Spacer(Modifier.height(16.dp))
            OutlinedButton(
                onClick = {
                    ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", ctx.packageName, null)))
                },
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Open app permissions") }
            Spacer(Modifier.height(8.dp))
            Text(
                "Knowledge packs are plain files you download yourself and import. The app checks their SHA-256 hashes.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Start,
            )
        }
    }
}

