package app.commonplace.ui

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.CloudOff
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.window.DialogWindowProvider
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.commonplace.CommonplaceApp
import java.util.Locale

@Composable
fun OfflineBadge(onClick: () -> Unit, modifier: Modifier = Modifier) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        // Outlined, not filled: a quiet fact, so the red fill stays with the actions.
        color = Color.Transparent,
        contentColor = MaterialTheme.colorScheme.onSurfaceVariant,
        border = hairline(),
        modifier = modifier.testTag("offline_badge"),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.CloudOff, contentDescription = null, modifier = Modifier.size(16.dp), tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(6.dp))
            Text("Offline · no network permission", style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** Numbered citation disc used in source cards. */
@Composable
fun NumberDisc(n: UInt, modifier: Modifier = Modifier) {
    Box(
        modifier
            .size(22.dp)
            .clip(CircleShape)
            .background(MaterialTheme.colorScheme.primary),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            n.toString(),
            color = MaterialTheme.colorScheme.onPrimary,
            fontSize = 11.sp,
            // Literata has tall ascenders; trim the line box so the digit sits in the middle of the disc.
            style = MaterialTheme.typography.labelSmall.copy(lineHeight = 11.sp, lineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.Both)),
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    // c2sc draws the capitals as Literata's small caps, like a running head in a book.
    Text(
        text.uppercase(Locale.ROOT),
        style = MaterialTheme.typography.labelLarge.copy(fontFeatureSettings = "c2sc"),
        color = MaterialTheme.colorScheme.primary,
        letterSpacing = 1.6.sp,
        modifier = modifier,
    )
}

/** A sheet has its own window, which puts a white scrim under the 3-button nav bar (see MainActivity). */
@Composable
fun ClearSheetNavBar() {
    val view = LocalView.current
    SideEffect { (view.parent as? DialogWindowProvider)?.window?.isNavigationBarContrastEnforced = false }
}

/** The thin rule around paper cards. */
@Composable
fun hairline() = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant)

/** A section break between turns: two rules with a fleuron between them. With a [key], the rules draw out from it once. */
@Composable
fun Ornament(modifier: Modifier = Modifier, key: Any? = null) {
    val c = MaterialTheme.colorScheme
    val p = rememberSweep(key, durationMs = 520)
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        HorizontalDivider(Modifier.weight(1f).graphicsLayer { scaleX = p; transformOrigin = TransformOrigin(1f, 0.5f) }, color = c.outlineVariant)
        Text("❦", color = c.outline, fontSize = 18.sp, modifier = Modifier.padding(horizontal = 14.dp))
        HorizontalDivider(Modifier.weight(1f).graphicsLayer { scaleX = p; transformOrigin = TransformOrigin(0f, 0.5f) }, color = c.outlineVariant)
    }
}

/** A thin rule that fills with ink as work goes on. [progress] is read while drawing, so an animation redraws only the rule. */
@Composable
fun InkRule(progress: () -> Float, modifier: Modifier = Modifier) {
    val c = MaterialTheme.colorScheme
    Box(
        modifier.fillMaxWidth().height(2.dp).background(c.outlineVariant).drawBehind {
            val w = size.width * progress().coerceIn(0f, 1f)
            val x = if (layoutDirection == LayoutDirection.Rtl) size.width - w else 0f
            drawRect(c.primary, Offset(x, 0f), Size(w, size.height))
        },
    )
}

/** The ink rule of a model load, with the percentage while llama.cpp reports it. It shows nothing when no load runs. */
@Composable
fun ModelLoadRule(modifier: Modifier = Modifier) {
    val holder = (LocalContext.current.applicationContext as CommonplaceApp).engine
    val load by holder.loadProgress.collectAsState()
    val current = load ?: return
    val shown by animateFloatAsState(current.fraction, tween(160, easing = LinearEasing), label = "model_load")
    Row(modifier.fillMaxWidth().testTag("model_load"), verticalAlignment = Alignment.CenterVertically) {
        InkRule({ shown }, Modifier.weight(1f))
        current.percent?.let {
            Spacer(Modifier.width(10.dp))
            Text("$it%", style = MaterialTheme.typography.labelMedium.copy(fontFeatureSettings = "tnum"), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * Text with [mark] under the highlighter. With a [sweepKey], the highlighter runs along the lines of the mark once;
 * the words take their ink at once, so the text never changes color mid-read.
 */
@Composable
fun MarkedText(
    text: String,
    mark: String,
    style: TextStyle,
    modifier: Modifier = Modifier,
    sweepKey: Any? = null,
    maxLines: Int = Int.MAX_VALUE,
    overflow: TextOverflow = TextOverflow.Clip,
    scrollToMark: Boolean = false,
) {
    val ex = LocalExtra.current
    val start = if (mark.isBlank()) -1 else text.indexOf(mark)
    if (start < 0) {
        Text(text, style = style, modifier = modifier, maxLines = maxLines, overflow = overflow)
        return
    }
    val end = start + mark.length
    val annotated = remember(text, mark, ex.onHighlight) {
        buildAnnotatedString {
            append(text.substring(0, start))
            withStyle(SpanStyle(color = ex.onHighlight)) { append(mark) }
            append(text.substring(end))
        }
    }
    val sweep = rememberSweep(sweepKey, delayMs = 180, durationMs = 620)
    var layout by remember(text) { mutableStateOf<TextLayoutResult?>(null) }
    val requester = remember { BringIntoViewRequester() }
    if (scrollToMark) LaunchedEffect(layout) {
        val l = layout ?: return@LaunchedEffect
        val first = l.getLineForOffset(start)
        val last = l.getLineForOffset(end - 1)
        // One line of margin keeps the mark clear of whatever pins the scroll area's edge.
        requester.bringIntoView(Rect(0f, l.getLineTop(first), l.size.width.toFloat(), l.getLineBottom(last) + (l.getLineBottom(last) - l.getLineTop(last))))
    }
    Text(
        annotated,
        style = style,
        maxLines = maxLines,
        overflow = overflow,
        onTextLayout = { layout = it },
        modifier = modifier.then(if (scrollToMark) Modifier.bringIntoViewRequester(requester) else Modifier).drawBehind {
            val l = layout ?: return@drawBehind
            val first = l.getLineForOffset(start)
            val last = l.getLineForOffset(end - 1).coerceAtMost(l.lineCount - 1)
            val spans = (first..last).map { line ->
                val a = if (line == first) l.getHorizontalPosition(start, true) else l.getLineLeft(line)
                val b = if (line == last) l.getHorizontalPosition(end, true) else l.getLineRight(line)
                Triple(line, minOf(a, b), maxOf(a, b))
            }
            var ink = spans.sumOf { (it.third - it.second).toDouble() }.toFloat() * sweep
            for ((line, x0, x1) in spans) {
                val w = minOf(ink, x1 - x0)
                if (w <= 0f) break
                drawRect(ex.highlight, Offset(x0, l.getLineTop(line)), Size(w, l.getLineBottom(line) - l.getLineTop(line)))
                ink -= w
            }
        },
    )
}

/** A window of `text` around `highlight`, at most ~`max` characters, with ellipses. */
fun excerpt(text: String, highlight: String, max: Int = 420, lead: Int = 120): String {
    if (text.length <= max) return text
    val i = text.indexOf(highlight).coerceAtLeast(0)
    var start = (i - lead).coerceAtLeast(0)
    if (start > 0) {
        val space = text.indexOf(' ', start)
        if (space in start..i) start = space + 1
    }
    val end = (start + max).coerceAtMost(text.length)
    return (if (start > 0) "… " else "") + text.substring(start, end).trim() + if (end < text.length) " …" else ""
}

@Composable
fun StorageMeter(used: Long, cap: Long, modifier: Modifier = Modifier) {
    val target = (used.toDouble() / cap).toFloat().coerceIn(0f, 1f)
    val frac = target * rememberSweep("storage_meter", delayMs = 120, durationMs = 620)
    Column(modifier) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text("${bytes(used)} used", style = MaterialTheme.typography.titleMedium)
            Text("of ${bytes(cap)} limit", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(8.dp))
        LinearProgressIndicator(
            progress = { frac },
            modifier = Modifier.fillMaxWidth().height(10.dp).clip(RoundedCornerShape(5.dp)),
            drawStopIndicator = {},
            gapSize = 0.dp,
        )
    }
}

@Composable
fun ClickableRow(onClick: () -> Unit, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(modifier.fillMaxWidth().clickable(onClick = onClick)) { content() }
}

fun bytes(b: Long): String = when {
    b >= 1_000_000_000 -> String.format(Locale.ROOT, "%.1f GB", b / 1e9)
    b >= 1_000_000 -> String.format(Locale.ROOT, "%.0f MB", b / 1e6)
    b >= 1_000 -> String.format(Locale.ROOT, "%.0f KB", b / 1e3)
    else -> "$b B"
}

fun seconds(ms: Double): String = if (ms < 1000) String.format(Locale.ROOT, "%.0f ms", ms) else String.format(Locale.ROOT, "%.1f s", ms / 1000)

fun count(n: ULong): String = when {
    n >= 1_000_000u -> String.format(Locale.ROOT, "%.1fM", n.toDouble() / 1e6)
    n >= 1_000u -> String.format(Locale.ROOT, "%.0fk", n.toDouble() / 1e3)
    else -> n.toString()
}
