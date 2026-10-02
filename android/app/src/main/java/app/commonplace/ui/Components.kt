package app.commonplace.ui

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
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.Locale

@Composable
fun OfflineBadge(onClick: () -> Unit, modifier: Modifier = Modifier) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = MaterialTheme.colorScheme.primaryContainer,
        contentColor = MaterialTheme.colorScheme.onPrimaryContainer,
        modifier = modifier.testTag("offline_badge"),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(Icons.Outlined.CloudOff, contentDescription = null, modifier = Modifier.size(16.dp))
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
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(Locale.ROOT),
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        letterSpacing = 1.2.sp,
        modifier = modifier,
    )
}

/** Text with `highlight` marked in the evidence color. Falls back to plain text if not found. */
fun highlighted(text: String, highlight: String, bg: Color, fg: Color): AnnotatedString = buildAnnotatedString {
    val i = if (highlight.isBlank()) -1 else text.indexOf(highlight)
    if (i < 0) {
        append(text)
        return@buildAnnotatedString
    }
    append(text.substring(0, i))
    withStyle(SpanStyle(background = bg, color = fg)) { append(highlight) }
    append(text.substring(i + highlight.length))
}

/** A window of `text` around `highlight`, at most ~`max` characters, with ellipses. */
fun excerpt(text: String, highlight: String, max: Int = 420): String {
    if (text.length <= max) return text
    val i = text.indexOf(highlight).coerceAtLeast(0)
    var start = (i - 120).coerceAtLeast(0)
    if (start > 0) {
        val space = text.indexOf(' ', start)
        if (space in start..i) start = space + 1
    }
    val end = (start + max).coerceAtMost(text.length)
    return (if (start > 0) "… " else "") + text.substring(start, end).trim() + if (end < text.length) " …" else ""
}

@Composable
fun StorageMeter(used: Long, cap: Long, modifier: Modifier = Modifier) {
    val frac = (used.toDouble() / cap).toFloat().coerceIn(0f, 1f)
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
