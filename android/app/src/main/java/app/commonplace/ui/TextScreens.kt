package app.commonplace.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.commonplace.CommonplaceApp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private const val LICENSES_ASSET = "third_party_licenses.txt"

/** Open-source licenses of the app itself, from the asset that scripts/licenses.py generates. */
@Composable
fun LicensesScreen(onBack: () -> Unit) {
    val ctx = LocalContext.current
    val text by produceState<String?>(null) {
        value = withContext(Dispatchers.IO) {
            runCatching { ctx.assets.open(LICENSES_ASSET).bufferedReader().use { it.readText() } }.getOrElse { "Could not read the license list: ${it.message}" }
        }
    }
    TextScreen("Licenses", text, "licenses_view", onBack)
}

/** The `NOTICE.txt` of an installed pack: its credit, license and license texts. */
@Composable
fun NoticeScreen(packId: String, onBack: () -> Unit) {
    val holder = (LocalContext.current.applicationContext as CommonplaceApp).engine
    val text by produceState<String?>(null, packId) {
        value = withContext(Dispatchers.IO) {
            val notice = runCatching { holder.engineOrNull?.packNotice(packId) }.getOrNull().orEmpty()
            notice.ifBlank { "This pack has no notice file." }
        }
    }
    TextScreen("Notice · $packId", text, "notice_view", onBack)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TextScreen(title: String, text: String?, tag: String, onBack: () -> Unit) {
    Scaffold(topBar = {
        TopAppBar(
            title = { Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
        )
    }) { pad ->
        if (text == null) {
            Column(Modifier.fillMaxSize().padding(pad), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) { CircularProgressIndicator() }
            return@Scaffold
        }
        // Blocks of lines keep every layout small: a license file is hundreds of kilobytes.
        val blocks = remember(text) { text.lines().chunked(40).map { it.joinToString("\n") } }
        SelectionContainer {
            LazyColumn(Modifier.fillMaxSize().padding(pad).testTag(tag), contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp)) {
                items(blocks) { block ->
                    Text(block, fontFamily = FontFamily.Monospace, fontSize = 11.sp, lineHeight = 16.sp, color = MaterialTheme.colorScheme.onSurface)
                }
            }
        }
    }
}
