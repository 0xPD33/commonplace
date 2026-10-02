package app.commonplace.ui

import android.text.format.DateUtils
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
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
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.commonplace.engine.Conversations
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Saved conversations, newest first, searchable by any question or answer text. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HistoryScreen(vm: AskViewModel, onBack: () -> Unit) {
    var all by remember { mutableStateOf<List<Conversations.Summary>?>(null) }
    var search by rememberSaveable { mutableStateOf("") }
    LaunchedEffect(Unit) { all = withContext(Dispatchers.IO) { vm.history() } }

    Scaffold(topBar = {
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
            val shown = list.filter { search.isBlank() || it.text.contains(search.trim(), ignoreCase = true) }
            if (shown.isEmpty()) {
                Text(
                    if (list.isEmpty()) "No saved conversations yet." else "No conversation matches.",
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(24.dp),
                )
            }
            LazyColumn(Modifier.fillMaxSize().testTag("history_list")) {
                items(shown, key = { it.id }) { c ->
                    ClickableRow(onClick = {
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
                                vm.deleteConversation(c.id)
                                all = list.filter { it.id != c.id }
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
