package app.commonplace.ui

import android.content.Intent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.expandVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.MenuBook
import androidx.compose.material.icons.outlined.AutoAwesome
import androidx.compose.material.icons.outlined.Calculate
import androidx.compose.material.icons.outlined.CheckCircle
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material.icons.outlined.ErrorOutline
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Share
import androidx.compose.material.icons.outlined.StopCircle
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.foundation.clickable
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import uniffi.commonplace_ffi.AnswerCard
import uniffi.commonplace_ffi.AnswerSegment
import uniffi.commonplace_ffi.AskStage
import uniffi.commonplace_ffi.SourceItem
import uniffi.commonplace_ffi.TurnKind
import java.util.Locale

/** A source the user wants to read: the passage plus the sentence to highlight. */
data class OpenSource(val source: SourceItem, val highlight: String)

@Composable
fun TurnView(
    turn: Turn,
    showTimings: Boolean,
    canThinkHarder: Boolean,
    onOpen: (OpenSource) -> Unit,
    onThinkHarder: () -> Unit,
    onRetry: (() -> Unit)?,
    onAsk: ((String) -> Unit)?,
    onDraft: ((String) -> Unit)?,
    modifier: Modifier = Modifier,
) {
    Column(modifier.fillMaxWidth().animateContentSize().testTag("turn")) {
        Row(verticalAlignment = Alignment.Top) {
            if (turn.deep) {
                Icon(Icons.Outlined.Psychology, "Deep mode", tint = MaterialTheme.colorScheme.secondary, modifier = Modifier.padding(top = 6.dp, end = 8.dp).size(22.dp))
            }
            Text(turn.query, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.testTag("turn_query"))
        }
        Spacer(Modifier.height(10.dp))
        StageLine(turn, showTimings)
        Spacer(Modifier.height(14.dp))

        val card = turn.card
        AnimatedVisibility(visible = card != null, enter = fadeIn() + expandVertically()) {
            if (card != null) EvidenceCard(card, onOpen)
        }

        val err = turn.error
        if (err != null) {
            Spacer(Modifier.height(14.dp))
            ErrorNote(err)
            if (onRetry != null) RetryButton(onRetry)
        } else if (turn.searchOnly && turn.done) {
            Spacer(Modifier.height(14.dp))
            val why = turn.modelError?.let { "The model could not load ($it)" } ?: "No language model is installed"
            InfoNote("$why, so this shows search results only. Add a model pack in Library for written answers.")
        } else if (!turn.searchOnly && card != null) {
            Spacer(Modifier.height(18.dp))
            if (turn.think && turn.thought.isNotEmpty()) {
                ThoughtBlock(turn)
                Spacer(Modifier.height(14.dp))
            }
            if (turn.segments.isEmpty() && turn.answering) {
                WritingPlaceholder()
            } else {
                AnswerText(turn, onOpen)
            }
            if (turn.segments.any { it is AnswerSegment.Unverified }) {
                Spacer(Modifier.height(10.dp))
                UnverifiedLegend()
            }
        }

        val sources = turn.sources.ifEmpty { card?.sources ?: emptyList() }
        if (sources.isNotEmpty() && (turn.done || turn.sources.isNotEmpty())) {
            Spacer(Modifier.height(18.dp))
            SectionLabel("Sources", Modifier.padding(bottom = 8.dp))
            SourcesRow(sources, numbered = turn.sources.isNotEmpty(), onOpen = onOpen)
        }

        if (turn.done && turn.error == null && !turn.searchOnly && turn.kind != TurnKind.CHAT) {
            Spacer(Modifier.height(8.dp))
            AnswerActions(turn, canThinkHarder && turn.kind == TurnKind.QUESTION, onThinkHarder, onRetry)
            if (onAsk != null && !turn.stopped) FollowUpChips(turn, onAsk, onDraft)
        }
    }
}

@Composable
private fun StageLine(turn: Turn, showTimings: Boolean) {
    val c = MaterialTheme.colorScheme
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.testTag("stage_line")) {
        if (turn.answering) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
            Spacer(Modifier.width(8.dp))
            Text(stageText(turn), style = MaterialTheme.typography.labelLarge, color = c.primary)
        } else if (turn.stopped) {
            Icon(Icons.Outlined.StopCircle, null, tint = c.outline, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(6.dp))
            Text("Stopped", style = MaterialTheme.typography.labelLarge, color = c.onSurfaceVariant)
        } else if (turn.error == null && turn.kind != TurnKind.CHAT) {
            Icon(Icons.Outlined.CheckCircle, null, tint = LocalExtra.current.good, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(6.dp))
            val n = turn.sources.size.takeIf { it > 0 } ?: turn.card?.sources?.size ?: 0
            val t = turn.timing
            val text = buildString {
                append(if (turn.searchOnly) "Found $n passages" else "Answered from $n sources")
                if (t != null) append(" · ${seconds(t.totalMs)}")
            }
            Text(text, style = MaterialTheme.typography.labelLarge, color = c.onSurfaceVariant)
        }
    }
    val t = turn.timing
    if (showTimings && t != null) {
        Spacer(Modifier.height(4.dp))
        val parts = buildList {
            add("card ${seconds(t.cardMs)}")
            t.ttftMs?.let { add("first word ${seconds(it)}") }
            // A stop after a token or two leaves a meaningless rate.
            if (!turn.stopped) t.decodeTps?.let { add(String.format(Locale.ROOT, "%.1f tok/s", it)) }
            if (t.cachedTokens > 0u) add("prefix cached")
            if (t.plannerUsed) add("planned")
        }
        Text(parts.joinToString(" · "), style = MaterialTheme.typography.labelSmall, color = c.outline, modifier = Modifier.testTag("timings"))
    }
}

private fun stageText(turn: Turn): String = if (turn.loadingModel) {
    if (turn.deep) "Loading the deep model… this takes a minute" else "Loading the model…"
} else when (turn.stage) {
    AskStage.SEARCHING -> "Searching your library…"
    AskStage.PLANNING -> "Breaking the question down…"
    AskStage.READING -> "Reading ${turn.stageDetail} sources…"
    AskStage.COMPUTING -> "Working out the numbers…"
    AskStage.THINKING -> "Thinking it through…"
    // Reformat and small-talk turns read no evidence; their card is empty.
    AskStage.WRITING -> if (turn.segments.isEmpty() && turn.card?.top != null) "Reading the evidence…" else "Writing…"
    AskStage.DONE -> "Finishing…"
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun EvidenceCard(card: AnswerCard, onOpen: (OpenSource) -> Unit) {
    val top = card.top ?: return
    val ex = LocalExtra.current
    val c = MaterialTheme.colorScheme
    Surface(
        onClick = { onOpen(OpenSource(top, card.highlight)) },
        shape = RoundedCornerShape(20.dp),
        color = c.surfaceContainerLow,
        border = BorderStroke(1.dp, c.outlineVariant),
        modifier = Modifier.fillMaxWidth().testTag("evidence_card"),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.AutoMirrored.Outlined.MenuBook, null, tint = c.primary, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Column(Modifier.weight(1f)) {
                    Text(top.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    val sub = listOf(top.section, top.packTitle).filter { it.isNotBlank() }.joinToString(" · ")
                    Text(sub, style = MaterialTheme.typography.labelSmall, color = c.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                if (card.fromCards) {
                    Text("Key facts", style = MaterialTheme.typography.labelSmall, color = c.secondary)
                }
            }
            card.featured?.let { f ->
                // The reader's short answer; it can be wrong, so its source is always named.
                Spacer(Modifier.height(10.dp))
                Text(f.text, style = MaterialTheme.typography.headlineSmall, color = c.primary, modifier = Modifier.testTag("featured"))
                Text("Quick answer from ${f.source.title}", style = MaterialTheme.typography.labelSmall, color = c.onSurfaceVariant)
            }
            Spacer(Modifier.height(10.dp))
            val body = remember(card) { excerpt(card.topText, card.highlight) }
            Text(
                highlighted(body, card.highlight, ex.highlight, ex.onHighlight),
                style = ReadingStyle.copy(fontSize = 16.sp, lineHeight = 25.sp),
                maxLines = 7,
                overflow = TextOverflow.Ellipsis,
            )
            if (card.facts.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                FactTable(card)
            }
            if (card.computed.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    for (x in card.computed) {
                        AssistChip(
                            onClick = {},
                            label = { Text(x) },
                            leadingIcon = { Icon(Icons.Outlined.Calculate, null, Modifier.size(AssistChipDefaults.IconSize)) },
                        )
                    }
                }
            }
        }
    }
}

/** Wikidata facts as label/value rows: the first three, the rest behind "Show more". */
@Composable
private fun FactTable(card: AnswerCard) {
    val names = card.facts.map { it.entity }.distinct()
    if (names.size >= 2) {
        CompareTable(card, names)
        return
    }
    val c = MaterialTheme.colorScheme
    var expanded by remember(card) { mutableStateOf(false) }
    val shown = if (expanded) card.facts else card.facts.take(3)
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.surfaceContainerHigh).padding(horizontal = 12.dp, vertical = 8.dp)
            .testTag("fact_table"),
    ) {
        for (f in shown) {
            Row(Modifier.padding(vertical = 3.dp), verticalAlignment = Alignment.Top) {
                val label = if (card.entities.size > 1) "${f.entity} · ${f.label}" else f.label
                Text(label, style = MaterialTheme.typography.labelMedium, color = c.onSurfaceVariant, modifier = Modifier.weight(0.42f).padding(top = 1.dp))
                Spacer(Modifier.width(8.dp))
                Text(
                    f.value + (f.`when`?.let { " ($it)" } ?: ""),
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    modifier = Modifier.weight(0.58f),
                )
            }
        }
        if (card.facts.size > 3) {
            Text(
                if (expanded) "Show less" else "Show ${card.facts.size - 3} more",
                style = MaterialTheme.typography.labelMedium,
                color = c.primary,
                modifier = Modifier.clickable { expanded = !expanded }.padding(vertical = 6.dp),
            )
        }
        Text("Wikidata", style = MaterialTheme.typography.labelSmall, color = c.outline)
    }
}

/** Facts of several entities side by side: one row per property, one column per entity. */
@Composable
private fun CompareTable(card: AnswerCard, names: List<String>) {
    val c = MaterialTheme.colorScheme
    val labels = card.facts.map { it.label }.distinct()
    var expanded by remember(card) { mutableStateOf(false) }
    val cell = Modifier.padding(start = 8.dp)
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.surfaceContainerHigh).padding(horizontal = 12.dp, vertical = 8.dp)
            .testTag("fact_table"),
    ) {
        Row(Modifier.padding(vertical = 3.dp)) {
            Spacer(Modifier.weight(0.3f))
            for (n in names) {
                Text(n, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold, modifier = cell.weight(0.7f / names.size))
            }
        }
        for (l in if (expanded) labels else labels.take(4)) {
            Row(Modifier.padding(vertical = 3.dp), verticalAlignment = Alignment.Top) {
                Text(l, style = MaterialTheme.typography.labelMedium, color = c.onSurfaceVariant, modifier = Modifier.weight(0.3f).padding(top = 1.dp))
                for (n in names) {
                    val f = card.facts.firstOrNull { it.entity == n && it.label == l }
                    Text(
                        f?.let { it.value + (it.`when`?.let { w -> " ($w)" } ?: "") } ?: "—",
                        style = MaterialTheme.typography.bodyMedium,
                        fontWeight = FontWeight.Medium,
                        modifier = cell.weight(0.7f / names.size),
                    )
                }
            }
        }
        if (labels.size > 4) {
            Text(
                if (expanded) "Show less" else "Show ${labels.size - 4} more",
                style = MaterialTheme.typography.labelMedium,
                color = c.primary,
                modifier = Modifier.clickable { expanded = !expanded }.padding(vertical = 6.dp),
            )
        }
        Text("Wikidata", style = MaterialTheme.typography.labelSmall, color = c.outline)
    }
}

@Composable
private fun AnswerText(turn: Turn, onOpen: (OpenSource) -> Unit) {
    val c = MaterialTheme.colorScheme
    val colors = AnswerColors(cite = c.primary, unverified = LocalExtra.current.unverified, codeBackground = c.surfaceContainerHigh)
    val open by rememberUpdatedState(onOpen)
    val blocks = remember(turn.segments, turn.answering, colors) {
        answerBlocks(turn.segments, colors, cursor = turn.answering) { n, sentence -> turn.source(n)?.let { open(OpenSource(it, sentence)) } }
    }
    val body = MaterialTheme.typography.bodyLarge
    SelectionContainer {
        Column(Modifier.fillMaxWidth().testTag("answer_text")) {
            blocks.forEachIndexed { i, b ->
                if (i > 0) Spacer(Modifier.height(if (b.gapBefore) 14.dp else if (b.kind == AnswerBlock.Kind.Item) 4.dp else 8.dp))
                when (b.kind) {
                    AnswerBlock.Kind.Heading -> Text(b.text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(top = 4.dp))
                    AnswerBlock.Kind.Paragraph -> Text(b.text, style = body)
                    AnswerBlock.Kind.Item -> Row(Modifier.padding(start = (b.level * 18).dp)) {
                        Text(b.marker, style = body, color = c.primary, fontWeight = FontWeight.SemiBold, modifier = Modifier.widthIn(min = 22.dp).padding(end = 6.dp))
                        Text(b.text, style = body)
                    }
                }
            }
        }
    }
}

@Composable
private fun WritingPlaceholder() {
    val t = rememberInfiniteTransition(label = "shimmer")
    val a by t.animateFloat(0.25f, 0.6f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "alpha")
    Column(Modifier.fillMaxWidth().testTag("writing_placeholder")) {
        for (w in listOf(1f, 0.92f, 0.7f)) {
            Box(
                Modifier.fillMaxWidth(w).height(14.dp).clip(RoundedCornerShape(7.dp)).alpha(a)
                    .background(MaterialTheme.colorScheme.surfaceContainerHighest),
            )
            Spacer(Modifier.height(10.dp))
        }
    }
}

@Composable
private fun UnverifiedLegend() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(Icons.Outlined.Info, null, tint = LocalExtra.current.unverified, modifier = Modifier.size(14.dp))
        Spacer(Modifier.width(6.dp))
        Text(
            "Underlined sentences contain numbers that are not in the sources. Check them.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
fun SourcesRow(sources: List<SourceItem>, numbered: Boolean, onOpen: (OpenSource) -> Unit) {
    val c = MaterialTheme.colorScheme
    LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(end = 16.dp), modifier = Modifier.testTag("sources_row")) {
        itemsIndexed(sources) { i, s ->
            Surface(
                onClick = { onOpen(OpenSource(s, "")) },
                shape = RoundedCornerShape(16.dp),
                color = c.surfaceContainer,
                modifier = Modifier.width(232.dp).height(118.dp),
            ) {
                Column(Modifier.padding(12.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        NumberDisc(if (numbered) s.n else (i + 1).toUInt())
                        Spacer(Modifier.width(8.dp))
                        Text(s.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                    if (s.section.isNotBlank()) {
                        Text(s.section, style = MaterialTheme.typography.labelSmall, color = c.primary, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 4.dp))
                    }
                    Spacer(Modifier.height(4.dp))
                    Text(s.snippet, style = MaterialTheme.typography.bodySmall, color = c.onSurfaceVariant, maxLines = 3, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun AnswerActions(turn: Turn, canThinkHarder: Boolean, onThinkHarder: () -> Unit, onRetry: (() -> Unit)?) {
    val clipboard = LocalClipboardManager.current
    val context = LocalContext.current
    val tint = MaterialTheme.colorScheme.onSurfaceVariant
    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth().offset(x = (-12).dp)) {
        IconButton(onClick = { clipboard.setText(AnnotatedString(shareText(turn, withQuestion = false))) }, modifier = Modifier.testTag("copy_answer")) {
            Icon(Icons.Outlined.ContentCopy, "Copy answer", Modifier.size(20.dp), tint = tint)
        }
        IconButton(
            onClick = {
                val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, shareText(turn, withQuestion = true))
                context.startActivity(Intent.createChooser(send, null))
            },
            modifier = Modifier.testTag("share_answer"),
        ) { Icon(Icons.Outlined.Share, "Share answer", Modifier.size(20.dp), tint = tint) }
        if (onRetry != null) {
            IconButton(onClick = onRetry, modifier = Modifier.testTag("retry")) { Icon(Icons.Outlined.Refresh, "Retry", Modifier.size(22.dp), tint = tint) }
        }
        Spacer(Modifier.weight(1f))
        if (canThinkHarder && !turn.deep) {
            OutlinedButton(onClick = onThinkHarder, modifier = Modifier.offset(x = 12.dp).testTag("think_harder")) {
                Icon(Icons.Outlined.AutoAwesome, null, Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text("Think harder · 1–2 min")
            }
        }
    }
}

/** Next steps under the latest answer: rewrite it, compare its main topic, or go on to a related article. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun FollowUpChips(turn: Turn, onAsk: (String) -> Unit, onDraft: ((String) -> Unit)?) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.testTag("follow_up_chips")) {
        SuggestionChip(onClick = { onAsk("Make it shorter") }, label = { Text("Shorter") })
        SuggestionChip(onClick = { onAsk("Explain it more simply") }, label = { Text("Simpler") })
        if (turn.kind != TurnKind.QUESTION) return@FlowRow
        val card = turn.card
        val topic = card?.entities?.firstOrNull()?.substringBefore(" (")
        if (onDraft != null && topic != null) {
            SuggestionChip(onClick = { onDraft("Compare $topic with ") }, label = { Text("Compare with…") }, modifier = Modifier.testTag("compare_with"))
        }
        // Other articles the answer drew on, from the main article's pack: a Q&A pack's titles are questions.
        val related = turn.sources.filter { it.packId == card?.top?.packId }.map { it.title }.distinct()
            .filter { it != card?.top?.title && card?.entities?.contains(it) != true }
        for (t in related.take(2)) {
            SuggestionChip(onClick = { onAsk("Tell me about $t") }, label = { Text("About ${t.substringBefore(" (")}") }, modifier = Modifier.testTag("related_topic"))
        }
    }
}

@Composable
private fun RetryButton(onRetry: () -> Unit) {
    TextButton(onClick = onRetry, modifier = Modifier.padding(top = 4.dp).testTag("retry")) {
        Icon(Icons.Outlined.Refresh, null, Modifier.size(18.dp))
        Spacer(Modifier.width(6.dp))
        Text("Try again")
    }
}

/** The answer with its numbered source list, for the clipboard or another app. */
private fun shareText(turn: Turn, withQuestion: Boolean): String = buildString {
    if (withQuestion) append(turn.query).append("\n\n")
    append(turn.answerText.trim())
    if (turn.sources.isNotEmpty()) {
        append("\n\nSources:\n")
        turn.sources.joinTo(this, "\n") { "[${it.n}] ${it.title}${if (it.section.isNotBlank()) " — ${it.section}" else ""} (${it.packTitle})" }
    }
}

@Composable
fun InfoNote(text: String) {
    Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.secondaryContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.Top) {
            Icon(Icons.Outlined.Info, null, Modifier.size(18.dp))
            Spacer(Modifier.width(10.dp))
            Text(text, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
fun ErrorNote(text: String) {
    Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth().testTag("error_note")) {
        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.Top) {
            Icon(Icons.Outlined.ErrorOutline, null, Modifier.size(18.dp), tint = MaterialTheme.colorScheme.onErrorContainer)
            Spacer(Modifier.width(10.dp))
            Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onErrorContainer)
        }
    }
}

/** The model's reasoning in thinking mode: open while it streams, then folded to one line. */
@Composable
private fun ThoughtBlock(turn: Turn) {
    var open by remember { mutableStateOf(false) }
    val c = MaterialTheme.colorScheme
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.surfaceContainerLow)
            .clickable(enabled = turn.thoughtDone) { open = !open }.padding(12.dp).testTag("thought"),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.Psychology, null, tint = c.secondary, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(6.dp))
            Text(
                if (turn.thoughtDone) (if (open) "Reasoning" else "Reasoning · tap to show") else "Thinking…",
                style = MaterialTheme.typography.labelLarge,
                color = c.secondary,
            )
        }
        if (open || !turn.thoughtDone) {
            Spacer(Modifier.height(6.dp))
            Text(turn.thought, style = MaterialTheme.typography.bodySmall, color = c.onSurfaceVariant)
        }
    }
}
