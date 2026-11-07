package app.commonplace.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch

/** A source over the answer, with the cited sentence marked, so checking a citation does not leave the conversation. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
@Composable
fun SourceSheet(open: OpenSource, onDismiss: () -> Unit, onDetails: () -> Unit, onArticle: (String, UInt, UInt) -> Unit) {
    val s = open.source
    val state = load("${s.packId}/${s.passageId}") { it.passage(s.packId, s.passageId) }
    // Open at the final height: when the content grows during the show animation, the sheet stays at the old height with its actions off screen.
    if (state is Loaded.Loading) return
    val c = MaterialTheme.colorScheme
    // No half-open state: the sheet is as tall as it needs, up to a limit that keeps the answer in view, so the actions always show.
    val tallest = LocalConfiguration.current.screenHeightDp.dp * 0.62f
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val scope = rememberCoroutineScope()
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        ClearSheetNavBar()
        // The sheet is its own window, so it needs its own testTagsAsResourceId (see MainActivity).
        Column(Modifier.semantics { testTagsAsResourceId = true }.heightIn(max = tallest).navigationBarsPadding().testTag("source_sheet")) {
            Row(Modifier.fillMaxWidth().padding(start = 24.dp, end = 12.dp, bottom = 8.dp)) {
                Text(s.title, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f).padding(top = 4.dp, end = 8.dp))
                IconButton(onClick = { scope.launch { sheetState.hide() }.invokeOnCompletion { onDismiss() } }, modifier = Modifier.testTag("sheet_close")) {
                    Icon(Icons.Outlined.Close, "Close")
                }
            }
            Column(Modifier.weight(1f, fill = false).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp).padding(bottom = 14.dp)) {
                if (s.section.isNotBlank()) Text(s.section, style = MaterialTheme.typography.titleSmall, color = c.onSurfaceVariant)
                Text(listOf(s.packTitle, s.license).filter { it.isNotBlank() }.joinToString(" · "), style = MaterialTheme.typography.labelSmall, color = c.outline, modifier = Modifier.padding(top = 2.dp))
                Spacer(Modifier.height(14.dp))
                when (val p = state) {
                    Loaded.Loading -> Unit
                    is Loaded.Err -> ErrorNote(p.msg)
                    is Loaded.Ok -> {
                        val v = p.value
                        val mark = remember(v, open.highlight) { bestMatch(v.text, open.highlight) }
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            for (para in v.text.split('\n').filter { it.isNotBlank() }) {
                                MarkedText(para, mark, ReadingStyle.copy(fontSize = 16.sp, lineHeight = 26.sp), sweepKey = "sheet-${s.packId}/${s.passageId}/${open.highlight}/$para", scrollToMark = true)
                            }
                        }
                    }
                }
            }
            (state as? Loaded.Ok)?.value?.let { v ->
                HorizontalDivider(color = c.outlineVariant)
                Column(Modifier.padding(start = 24.dp, end = 24.dp, top = 12.dp, bottom = 4.dp)) {
                    Button(onClick = { onArticle(v.packId, v.articleId, v.passageId) }, modifier = Modifier.fillMaxWidth().height(52.dp).testTag("sheet_article")) { Text("Read the full article") }
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                        TextButton(onClick = onDetails, modifier = Modifier.testTag("source_details")) { Text("License and link") }
                    }
                }
            }
        }
    }
}
