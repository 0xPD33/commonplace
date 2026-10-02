package app.commonplace.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.Article
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.commonplace.CommonplaceApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import uniffi.commonplace_ffi.ArticleView
import uniffi.commonplace_ffi.PassageView

private sealed interface Loaded<out T> {
    data object Loading : Loaded<Nothing>
    data class Ok<T>(val value: T) : Loaded<T>
    data class Err(val msg: String) : Loaded<Nothing>
}

@Composable
private fun <T> load(key: Any, block: (uniffi.commonplace_ffi.CommonplaceEngine) -> T): Loaded<T> {
    val holder = (LocalContext.current.applicationContext as CommonplaceApp).engine
    val state by produceState<Loaded<T>>(Loaded.Loading, key) {
        value = withContext(Dispatchers.IO) {
            val e = holder.engineOrNull ?: return@withContext Loaded.Err("Engine not ready")
            runCatching { Loaded.Ok(block(e)) }.getOrElse { Loaded.Err(it.message ?: it.toString()) }
        }
    }
    return state
}

@Composable
private fun Breadcrumb(parts: List<String>) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        parts.filter { it.isNotBlank() }.forEachIndexed { i, p ->
            if (i > 0) Icon(Icons.Outlined.ChevronRight, null, Modifier.size(14.dp), tint = MaterialTheme.colorScheme.outline)
            Text(p, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PassageScreen(packId: String, passageId: UInt, highlight: String, onBack: () -> Unit, onArticle: (String, UInt, UInt) -> Unit) {
    val state = load("$packId/$passageId") { it.passage(packId, passageId) }
    Scaffold(topBar = {
        TopAppBar(
            title = { Text("Source") },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
        )
    }) { pad ->
        when (val s = state) {
            Loaded.Loading -> Column(Modifier.fillMaxSize().padding(pad), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) { CircularProgressIndicator() }
            is Loaded.Err -> Column(Modifier.padding(pad).padding(20.dp)) { ErrorNote(s.msg) }
            is Loaded.Ok -> PassageBody(pad, s.value, highlight) { onArticle(s.value.packId, s.value.articleId, s.value.passageId) }
        }
    }
}

@Composable
private fun PassageBody(pad: PaddingValues, p: PassageView, highlight: String, onArticle: () -> Unit) {
    val ex = LocalExtra.current
    val mark = remember(p, highlight) { bestMatch(p.text, highlight) }
    Column(
        Modifier.fillMaxSize().padding(pad).verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 12.dp).navigationBarsPadding()
            .testTag("passage_view"),
    ) {
        Breadcrumb(listOf(p.packTitle, p.title, p.section))
        Spacer(Modifier.height(14.dp))
        Text(p.title, style = MaterialTheme.typography.headlineMedium)
        if (p.section.isNotBlank()) {
            Text(p.section, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.height(18.dp))
        Text(highlighted(p.text, mark, ex.highlight, ex.onHighlight), style = ReadingStyle)
        Spacer(Modifier.height(28.dp))
        Button(onClick = onArticle, modifier = Modifier.fillMaxWidth().height(52.dp).testTag("open_article")) {
            Icon(Icons.AutoMirrored.Outlined.Article, null)
            Spacer(Modifier.width(8.dp))
            Text("Read the full article")
        }
        Spacer(Modifier.height(20.dp))
        Text(
            "Text from ${p.packTitle}, ${p.license}. Passage ${p.passageId}.",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.outline,
        )
    }
}

/**
 * The sentence of `text` that shares the most words with `hint` (the answer sentence that cited it).
 * Returns "" when nothing overlaps enough to be worth highlighting.
 */
fun bestMatch(text: String, hint: String): String {
    if (hint.isBlank()) return ""
    val want = hint.lowercase().split(Regex("\\W+")).filter { it.length > 3 }.toSet()
    if (want.isEmpty()) return ""
    val sentences = text.split(Regex("(?<=[.!?])\\s+(?=[A-Z0-9\"(])")).filter { it.isNotBlank() }
    val best = sentences.maxByOrNull { s -> s.lowercase().split(Regex("\\W+")).count { it in want } } ?: return ""
    val hits = best.lowercase().split(Regex("\\W+")).count { it in want }
    return if (hits >= 2 || hits == want.size) best else ""
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ArticleScreen(packId: String, articleId: UInt, focus: UInt, onBack: () -> Unit) {
    val state = load("$packId/a$articleId") { it.article(packId, articleId) }
    Scaffold(topBar = {
        TopAppBar(
            title = { Text((state as? Loaded.Ok)?.value?.title ?: "Article", maxLines = 1, overflow = TextOverflow.Ellipsis) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
        )
    }) { pad ->
        when (val s = state) {
            Loaded.Loading -> Column(Modifier.fillMaxSize().padding(pad), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) { CircularProgressIndicator() }
            is Loaded.Err -> Column(Modifier.padding(pad).padding(20.dp)) { ErrorNote(s.msg) }
            is Loaded.Ok -> ArticleBody(pad, s.value, focus)
        }
    }
}

@Composable
private fun ArticleBody(pad: PaddingValues, a: ArticleView, focus: UInt) {
    val list = rememberLazyListState()
    val focusIndex = a.paragraphs.indexOfFirst { it.passageId == focus }
    var scrolled by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(focusIndex) {
        if (focusIndex > 0 && !scrolled) {
            list.scrollToItem(focusIndex + 1, -120)
            scrolled = true
        }
    }
    val ex = LocalExtra.current
    LazyColumn(
        state = list,
        modifier = Modifier.fillMaxSize().padding(pad).testTag("article_view"),
        contentPadding = PaddingValues(horizontal = 24.dp, vertical = 12.dp),
    ) {
        item {
            Breadcrumb(listOf(a.packTitle, a.title))
            Spacer(Modifier.height(10.dp))
            Text(a.title, style = MaterialTheme.typography.headlineMedium)
            if (a.oneliner.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(a.oneliner, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 3, overflow = TextOverflow.Ellipsis)
            }
            Spacer(Modifier.height(12.dp))
        }
        itemsIndexed(a.paragraphs) { i, p ->
            val newSection = i == 0 || a.paragraphs[i - 1].section != p.section
            Column {
                if (newSection && p.section.isNotBlank()) {
                    Spacer(Modifier.height(18.dp))
                    Text(p.section.substringAfterLast(" > "), style = MaterialTheme.typography.titleLarge)
                    Spacer(Modifier.height(6.dp))
                }
                val focused = p.passageId == focus
                Text(
                    p.text,
                    style = ReadingStyle,
                    modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp)
                        .then(if (focused) Modifier.clip(RoundedCornerShape(8.dp)).background(ex.highlight).padding(8.dp) else Modifier),
                    color = if (focused) ex.onHighlight else MaterialTheme.colorScheme.onSurface,
                )
            }
        }
        item {
            Spacer(Modifier.height(24.dp))
            Text("${a.attribution}. ${a.license}.", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
            Spacer(Modifier.height(32.dp))
        }
    }
}
