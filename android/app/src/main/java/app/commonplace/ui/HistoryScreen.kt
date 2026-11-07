package app.commonplace.ui

import android.text.format.DateUtils
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.commonplace.engine.Conversations
import kotlinx.coroutines.launch

/** Saved conversations, newest first, searchable by any question or answer text. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(vm: AskViewModel, onBack: () -> Unit) {
    var all by remember { mutableStateOf<List<Conversations.Summary>?>(null) }
    var search by rememberSaveable { mutableStateOf("") }
    var reading by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        // Merge into the current list, so a row deleted while older pages load stays gone.
        vm.historyPages().collect { page -> all = (all.orEmpty() + page).sortedByDescending { it.updated } }
        all = all.orEmpty()
        reading = false
    }
    val snack = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    // Hidden from the list until the snackbar ends: only then does the file go. Undo needs nothing else.
    val pending = remember { mutableStateListOf<Long>() }
    val listState = rememberLazyListState()
    val latest by rememberUpdatedState(vm)
    DisposableEffect(Unit) { onDispose { pending.forEach { latest.deleteConversation(it) } } }

    Scaffold(snackbarHost = { SnackbarHost(snack) }, topBar = {
        TopAppBar(
            title = { Text("History") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
        )
    }) { pad ->
        Column(Modifier.fillMaxSize().padding(pad)) {
            OutlinedTextField(
                value = search,
                onValueChange = { search = it },
                placeholder = { Text("Search conversations") },
                leadingIcon = { Icon(Icons.Outlined.Search, null) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp).testTag("history_search"),
            )
            val list = all ?: return@Column
            val kept = list.filter { it.id !in pending }
            val shown = kept.filter { search.isBlank() || it.text.contains(search.trim(), ignoreCase = true) }
            if (reading && search.isNotBlank()) {
                Text(
                    "Still searching older conversations…",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 20.dp, vertical = 4.dp).testTag("history_reading"),
                )
            }
            if (shown.isEmpty() && !reading) {
                Text(
                    if (kept.isEmpty()) "No saved conversations yet." else "No conversation matches.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(24.dp),
                )
            }
            LazyColumn(Modifier.fillMaxSize().testTag("history_list"), state = listState) {
                items(shown, key = { it.id }) { c ->
                    ClickableRow(modifier = Modifier.animateItem(), onClick = {
                        vm.open(c.id)
                        onBack()
                    }) {
                        Row(Modifier.padding(start = 20.dp, end = 8.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(c.title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                val questions = if (c.turns == 1) "1 question" else "${c.turns} questions"
                                Text(
                                    "${DateUtils.getRelativeTimeSpanString(c.updated)} · $questions",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            IconButton(onClick = {
                                pending += c.id
                                snack.currentSnackbarData?.dismiss()
                                scope.launch {
                                    val undone = snack.showSnackbar("Conversation deleted", actionLabel = "Undo", duration = SnackbarDuration.Long) == SnackbarResult.ActionPerformed
                                    if (!undone) {
                                        vm.deleteConversation(c.id)
                                        all = all?.filter { it.id != c.id }
                                    }
                                    pending -= c.id
                                    if (undone) {
                                        // The list stays anchored on the row below, so a row restored at the top would land out of view.
                                        val at = shown.indexOfFirst { it.id == c.id }
                                        withFrameNanos { }
                                        if (at >= 0 && at <= listState.firstVisibleItemIndex) listState.animateScrollToItem(at)
                                    }
                                }
                            }, modifier = Modifier.testTag("history_delete")) {
                                Icon(Icons.Outlined.Delete, "Delete conversation")
                            }
                        }
                    }
                }
            }
        }
    }
}
