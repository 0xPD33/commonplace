package app.commonplace.ui

import android.content.Context
import android.provider.DocumentsContract
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.testTagsAsResourceId
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.commonplace.engine.CatalogPack

/** The multi-file picker, opened on the Downloads folder. Tree access to Downloads is not allowed since Android 11. */
class PickFromDownloads : ActivityResultContracts.OpenMultipleDocuments() {
    override fun createIntent(context: Context, input: Array<String>) =
        super.createIntent(context, input).putExtra(
            DocumentsContract.EXTRA_INITIAL_URI,
            DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Download"),
        )
}

/** The single-file picker for My documents, opened on the Downloads folder. */
class PickDocument : ActivityResultContracts.OpenDocument() {
    override fun createIntent(context: Context, input: Array<String>) =
        super.createIntent(context, input).putExtra(
            DocumentsContract.EXTRA_INITIAL_URI,
            DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Download"),
        )
}

@Composable
fun AvailableRow(p: CatalogPack, onClick: () -> Unit) {
    Surface(onClick = onClick, shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerLow, border = hairline(), modifier = Modifier.fillMaxWidth().testTag("available_row")) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(p.title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                if (p.recommended) {
                    Spacer(Modifier.width(8.dp))
                    Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.primaryContainer) {
                        Text("Starter", style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp))
                    }
                }
            }
            Text(p.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(sizes(p), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalComposeUiApi::class)
@Composable
fun PackSheet(p: CatalogPack, onDismiss: () -> Unit, onDownload: (String) -> Unit, onInstall: () -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)) {
        // The sheet is its own window, so it needs its own testTagsAsResourceId (see MainActivity).
        Column(Modifier.semantics { testTagsAsResourceId = true }.padding(horizontal = 20.dp).padding(bottom = 24.dp).verticalScroll(rememberScrollState()).testTag("pack_sheet")) {
            Text(p.title, style = MaterialTheme.typography.titleLarge)
            Text(p.description, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(sizes(p), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline, modifier = Modifier.padding(top = 4.dp))
            Spacer(Modifier.height(16.dp))
            InfoNote(if (p.files.size == 1) "Download the file below, then tap Install from Downloads." else "Download every file below, then tap Install from Downloads.")
            Spacer(Modifier.height(12.dp))
            for (f in p.files) {
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(f.name, style = MaterialTheme.typography.bodyMedium)
                        Text(bytes(f.bytes), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
                    }
                    OutlinedButton(onClick = { onDownload(f.url) }, modifier = Modifier.testTag("download_file")) { Text("Download") }
                }
            }
            Spacer(Modifier.height(16.dp))
            Button(onClick = onInstall, modifier = Modifier.fillMaxWidth().height(52.dp).testTag("sheet_install")) { Text("Install from Downloads") }
        }
    }
}

private fun sizes(p: CatalogPack) = "${bytes(p.downloadBytes)} download · ${bytes(p.installedBytes)} installed · ${p.license}"
