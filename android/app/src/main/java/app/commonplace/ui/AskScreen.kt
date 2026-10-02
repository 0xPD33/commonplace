package app.commonplace.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
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
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowForward
import androidx.compose.material.icons.outlined.AddComment
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.material3.Surface
import androidx.compose.material3.SuggestionChip
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
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextRange
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
import app.commonplace.engine.EngineState
import app.commonplace.engine.ModelState
import kotlinx.coroutines.launch
import uniffi.commonplace_ffi.LibraryInfo
import uniffi.commonplace_ffi.ModelKind

private val EXAMPLES = listOf(
    "Why is the sky blue?",
    "Compare the Nile and the Amazon",
    "How do vaccines train the immune system?",
    "What caused the fall of the Western Roman Empire?",
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
    val canThinkHarder = library?.models?.any { it.kind == ModelKind.DEEP } == true
    val micPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.toggleVoice() else vm.micDenied()
    }
    val context = LocalContext.current
    val onMic = {
        val granted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (granted || vm.listening) vm.toggleVoice() else micPermission.launch(Manifest.permission.RECORD_AUDIO)
    }
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
                        Text("Commonplace", style = MaterialTheme.typography.titleLarge)
                        ModelStatusLine(model, installed = library?.models?.isNotEmpty() == true)
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
                    onSend = { vm.ask() },
                    onStop = vm::stop,
                    thinking = vm.thinking,
                    onToggleThink = vm::toggleThinking,
                    listening = vm.listening,
                    voiceError = vm.voiceError,
                    onMic = if (vm.voiceAvailable) onMic else null,
                    focus = inputFocus,
                )
            }
        },
    ) { pad ->
        when (val s = engineState) {
            EngineState.Starting -> Centered(pad) {
                CircularProgressIndicator()
                Spacer(Modifier.height(16.dp))
                Text("Opening your library…", style = MaterialTheme.typography.bodyLarge)
            }
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
private fun ModelStatusLine(model: ModelState, installed: Boolean) {
    val (text, color) = when (model) {
        // Android unloads the model in the background; the next question loads it again.
        ModelState.None -> (if (installed) "Model loads on your next question" else "Search only · no model installed") to MaterialTheme.colorScheme.onSurfaceVariant
        is ModelState.Loading -> "Loading model…" to MaterialTheme.colorScheme.secondary
        is ModelState.Loaded -> "${if (model.kind == ModelKind.DEEP) "Deep model" else "Ready"} · ${model.backend.name.uppercase()}" to MaterialTheme.colorScheme.primary
        is ModelState.Failed -> "Model failed to load" to MaterialTheme.colorScheme.error
    }
    Text(text, style = MaterialTheme.typography.labelSmall, color = color, modifier = Modifier.testTag("model_status"))
}

@Composable
private fun Centered(pad: PaddingValues, content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(pad).padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) { content() }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Welcome(pad: PaddingValues, lib: LibraryInfo, recent: List<String>, onAsk: (String) -> Unit, onOffline: () -> Unit) {
    Column(
        Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 16.dp).testTag("welcome"),
    ) {
        Spacer(Modifier.height(24.dp))
        Text("What would you\nlike to know?", style = MaterialTheme.typography.displaySmall)
        Spacer(Modifier.height(12.dp))
        Text(
            "Answers come from the library on this phone, with sources you can open and check.",
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(16.dp))
        OfflineBadge(onOffline)
        Spacer(Modifier.height(20.dp))
        val passages = lib.packs.sumOf { it.passages.toLong() }.toULong()
        val names = lib.packs.joinToString(", ") { it.title }
        Surface(shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth()) {
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
        Spacer(Modifier.height(24.dp))
        SectionLabel("Try asking")
        Spacer(Modifier.height(8.dp))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.testTag("examples")) {
            for (q in EXAMPLES) SuggestionChip(onClick = { onAsk(q) }, label = { Text(q) })
        }
        if (recent.isNotEmpty()) {
            Spacer(Modifier.height(24.dp))
            SectionLabel("Recent")
            Spacer(Modifier.height(4.dp))
            for (q in recent.take(5)) {
                ClickableRow(onClick = { onAsk(q) }) {
                    Row(Modifier.padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Outlined.History, null, tint = MaterialTheme.colorScheme.outline, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(12.dp))
                        Text(q, style = MaterialTheme.typography.bodyLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
                HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            }
        }
    }
}

@Composable
private fun EmptyLibrary(pad: PaddingValues, onLibrary: () -> Unit, onOffline: () -> Unit) {
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
        Step(1, "Download the starter pack", "On any device, download the files of a pack (the parts and the .pack.json) from the Commonplace releases page into Downloads.")
        Step(2, "Import it here", "Tap Import in Library and select all the files. Commonplace checks every byte before it uses them.")
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
    Box(Modifier.fillMaxSize().padding(pad)) {
        LazyColumn(
            state = list,
            modifier = Modifier.fillMaxSize().testTag("conversation"),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 12.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(28.dp),
        ) {
            itemsIndexed(vm.turns, key = { _, t -> t.id }) { i, t ->
                Column {
                    if (i > 0) HorizontalDivider(Modifier.padding(bottom = 28.dp), color = MaterialTheme.colorScheme.outlineVariant)
                    val latest = i == vm.turns.lastIndex && !vm.busy
                    TurnView(
                        t, showTimings, canThinkHarder, onOpen,
                        onThinkHarder = { vm.thinkHarder(t) },
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
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 16.dp),
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
) {
    Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 2.dp, shadowElevation = 8.dp) {
        Row(
            Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Thinking mode: slower, the model reasons before it answers.
            IconToggleButton(checked = thinking, onCheckedChange = { onToggleThink() }, modifier = Modifier.testTag("think_toggle")) {
                Icon(
                    Icons.Outlined.Psychology,
                    if (thinking) "Thinking on" else "Thinking off",
                    tint = if (thinking) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
                )
            }
            // A draft set from outside (a chip, the voice transcript) puts the cursor after it.
            var field by remember { mutableStateOf(TextFieldValue(value, TextRange(value.length))) }
            if (field.text != value) field = TextFieldValue(value, TextRange(value.length))
            TextField(
                value = field,
                onValueChange = {
                    field = it
                    onValue(it.text)
                },
                placeholder = { Text(if (listening) "Listening…" else voiceError?.let { "Voice input failed: $it" } ?: "Ask your library…") },
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
            if (onMic != null && !busy) {
                IconToggleButton(checked = listening, onCheckedChange = { onMic() }, modifier = Modifier.testTag("mic_toggle")) {
                    Icon(
                        if (listening) Icons.Outlined.MicOff else Icons.Outlined.Mic,
                        if (listening) "Stop listening" else "Speak your question",
                        tint = if (listening) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
                    )
                }
            }
            Spacer(Modifier.width(8.dp))
            if (busy) {
                FilledIconButton(onClick = onStop, modifier = Modifier.size(52.dp).testTag("ask_stop")) { Icon(Icons.Outlined.Stop, "Stop") }
            } else {
                FilledIconButton(onClick = onSend, enabled = value.isNotBlank() && !listening, modifier = Modifier.size(52.dp).testTag("ask_send")) {
                    Icon(Icons.AutoMirrored.Outlined.ArrowForward, "Ask")
                }
            }
        }
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

