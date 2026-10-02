package app.commonplace.ui

import android.app.Application
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.automirrored.outlined.LibraryBooks
import androidx.compose.material.icons.outlined.Download
import androidx.compose.material.icons.outlined.DataObject
import androidx.compose.material.icons.outlined.Memory
import androidx.compose.material.icons.outlined.MoreVert
import androidx.compose.material.icons.outlined.Verified
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import app.commonplace.CommonplaceApp
import app.commonplace.engine.CatalogPack
import app.commonplace.engine.ModelState
import app.commonplace.engine.SelectedFile
import app.commonplace.engine.availablePacks
import app.commonplace.engine.loadCatalog
import app.commonplace.engine.planImport
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uniffi.commonplace_ffi.ImportListener
import uniffi.commonplace_ffi.ImportPart
import uniffi.commonplace_ffi.ModelKind

class ImportState {
    var running by mutableStateOf(false)
    var done by mutableStateOf(0L)
    var total by mutableStateOf(1L)
    var message by mutableStateOf("")
    val finishedParts = mutableStateListOf<String>()
}

/** One import at a time per process; its progress survives leaving and reopening Library. */
private val importState = ImportState()

class LibraryViewModel(app: Application) : AndroidViewModel(app) {
    private val holder = (app as CommonplaceApp).engine
    val import = importState
    var snackbar by mutableStateOf<String?>(null)

    /** Source files that were read and verified; offered for deletion afterwards. */
    var deletable by mutableStateOf<List<Pair<Uri, Long>>>(emptyList())

    /** What the last selection could not install: missing files and failed imports. */
    var problems by mutableStateOf<List<String>>(emptyList())

    /** Install every pack whose files are all among `uris`, one pack after the other. Unrelated files are ignored. */
    fun importUris(uris: List<Uri>, catalog: List<CatalogPack>) {
        if (uris.isEmpty() || import.running) return
        import.running = true
        problems = emptyList()
        val cr = getApplication<Application>().contentResolver
        // The process scope, not viewModelScope: leaving Library must not skip the library refresh.
        holder.scope.launch {
            val selected = uris.map { uri ->
                var name = uri.lastPathSegment ?: "part"
                var size = 0L
                runCatching {
                    cr.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
                        if (c.moveToFirst()) {
                            name = c.getString(0) ?: name
                            size = c.getLong(1)
                        }
                    }
                }
                SelectedFile(uri, name, size)
            }
            val plan = planImport(catalog, selected)
            val failed = plan.missing.toMutableList()
            if (plan.jobs.isEmpty() && failed.isEmpty()) failed += "None of the selected files belongs to a pack."
            val installed = mutableListOf<String>()
            val sources = mutableListOf<Pair<Uri, Long>>()
            plan.jobs.forEachIndexed { i, job ->
                val opened = mutableListOf<Pair<SelectedFile<Uri>, ParcelFileDescriptor>>()
                val result = runCatching {
                    for (f in job.files) cr.openFileDescriptor(f.handle, "r")?.let { opened += f to it }
                    withContext(Dispatchers.Main) {
                        import.done = 0
                        import.total = job.files.sumOf { it.size }.coerceAtLeast(1)
                        import.message = "Installing ${job.title}" + if (plan.jobs.size > 1) " (${i + 1} of ${plan.jobs.size})…" else "…"
                        import.finishedParts.clear()
                    }
                    val listener = object : ImportListener {
                        override fun onProgress(bytesDone: ULong, bytesTotal: ULong) {
                            holder.scope.launch(Dispatchers.Main) { import.done = bytesDone.toLong() }
                        }

                        override fun onPartDone(name: String) {
                            holder.scope.launch(Dispatchers.Main) { import.finishedParts += name }
                        }
                    }
                    // detachFd hands each descriptor to Rust, which closes it.
                    val parts = opened.map { (f, fd) -> ImportPart(f.name, fd.detachFd(), f.size.toULong()) }
                    opened.clear()
                    holder.engineOrNull!!.importPack(parts, listener)
                }
                opened.forEach { runCatching { it.second.close() } }
                result.onSuccess {
                    installed += it
                    sources += job.files.map { f -> f.handle to f.size }
                }.onFailure { failed += "${job.title}: import failed: ${it.message}" }
            }
            withContext(Dispatchers.Main) {
                import.running = false
                problems = failed
                if (installed.isNotEmpty()) snackbar = "Installed ${installed.joinToString(", ")}"
                deletable = sources
            }
            holder.refreshLibrary()
            if (installed.isNotEmpty()) holder.ensureModel(null)
        }
    }

    fun deleteSources() {
        val cr = getApplication<Application>().contentResolver
        val files = deletable
        deletable = emptyList()
        viewModelScope.launch(Dispatchers.IO) {
            val ok = files.count { (uri, _) -> runCatching { DocumentsContract.deleteDocument(cr, uri) }.getOrDefault(false) }
            withContext(Dispatchers.Main) { snackbar = "Deleted $ok of ${files.size} downloaded files" }
        }
    }

    fun remove(packId: String) = viewModelScope.launch(Dispatchers.IO) {
        val e = holder.engineOrNull ?: return@launch
        val r = runCatching { e.removePack(packId) }
        holder.refreshLibrary()
        // Removing a model pack unloads that model in Rust; bring the Kotlin state and the fallback model in line.
        if (!e.modelStatus().loaded && holder.model.value is ModelState.Loaded) holder.reloadModel()
        withContext(Dispatchers.Main) { snackbar = r.fold({ "Removed $packId" }, { "Could not remove: ${it.message}" }) }
    }

    fun verify(packId: String) = viewModelScope.launch(Dispatchers.IO) {
        withContext(Dispatchers.Main) { snackbar = "Checking $packId…" }
        val r = runCatching { holder.engineOrNull!!.verifyPack(packId) }
        withContext(Dispatchers.Main) {
            snackbar = r.fold({ if (it.isEmpty()) "$packId is intact" else "$packId: ${it.size} files damaged" }, { "Check failed: ${it.message}" })
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LibraryScreen(onBack: () -> Unit, vm: LibraryViewModel = viewModel()) {
    val ctx = LocalContext.current
    val holder = (ctx.applicationContext as CommonplaceApp).engine
    val lib by holder.library.collectAsState()
    val model by holder.model.collectAsState()
    val snack = remember { SnackbarHostState() }
    LaunchedEffect(vm.snackbar) { vm.snackbar?.let { snack.showSnackbar(it); vm.snackbar = null } }
    val catalog = remember { loadCatalog(ctx) }
    val picker = rememberLauncherForActivityResult(PickFromDownloads()) { vm.importUris(it, catalog) }
    var confirmRemove by remember { mutableStateOf<String?>(null) }
    var detail by remember { mutableStateOf<CatalogPack?>(null) }
    val installedIds = remember(lib) {
        lib?.let { l -> l.packs.map { it.packId } + l.models.map { it.packId } + listOfNotNull("wikidata-facts".takeIf { l.hasWikidata }) }.orEmpty().toSet()
    }
    val available = remember(catalog, installedIds) { if (lib == null) emptyList() else availablePacks(catalog, installedIds) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Library") },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back") } },
            )
        },
        floatingActionButton = {
            if (!vm.import.running) {
                ExtendedFloatingActionButton(
                    onClick = { picker.launch(arrayOf("*/*")) },
                    icon = { Icon(Icons.Outlined.Download, null) },
                    text = { Text("Install from Downloads") },
                    modifier = Modifier.testTag("import_pack"),
                )
            }
        },
        snackbarHost = { SnackbarHost(snack) },
    ) { pad ->
        val l = lib
        LazyColumn(
            Modifier.fillMaxSize().padding(pad).testTag("library_list"),
            contentPadding = PaddingValues(start = 20.dp, end = 20.dp, top = 8.dp, bottom = 96.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item {
                if (l != null) StorageMeter(l.totalBytes.toLong(), l.capBytes.toLong(), Modifier.padding(vertical = 8.dp))
            }
            if (vm.import.running) item { ImportProgress(vm.import) }
            if (vm.problems.isNotEmpty() && !vm.import.running) {
                item { ErrorNote("Not installed:\n" + vm.problems.joinToString("\n") { "• $it" }) }
            }
            if (available.isNotEmpty()) {
                item { SectionLabel("Get more", Modifier.padding(top = 12.dp)) }
                items(available, key = { "available-" + it.packId }) { p -> AvailableRow(p, onClick = { detail = p }) }
            }
            item { SectionLabel("Knowledge", Modifier.padding(top = 12.dp)) }
            if (l == null || l.packs.isEmpty()) {
                item {
                    InfoNote(
                        if (catalog.isEmpty()) "No knowledge packs yet. Download the parts of a pack and its .pack.json on any device, then tap Install from Downloads and select all of them."
                        else "No knowledge packs yet. Pick one under Get more, download its files in your browser, then tap Install from Downloads.",
                    )
                }
            }
            items(l?.packs ?: emptyList(), key = { it.packId }) { p ->
                PackRow(
                    icon = Icons.AutoMirrored.Outlined.LibraryBooks,
                    title = p.title,
                    subtitle = "${count(p.passages)} passages · ${count(p.articles)} articles · snapshot ${p.snapshotDate}",
                    detail = buildList {
                        add(bytes(p.sizeBytes.toLong()))
                        if (p.hasDense) add("semantic search")
                        if (p.hasCards) add("fact cards")
                        add(p.license)
                    }.joinToString(" · "),
                    onVerify = { vm.verify(p.packId) },
                    onRemove = { confirmRemove = p.packId },
                )
            }
            if (l?.hasWikidata == true) {
                item {
                    PackRow(Icons.Outlined.DataObject, "Wikidata facts", "Numbers and dates for people, places and things", bytes(l.wikidataSizeBytes.toLong()) + " · CC0",
                        onVerify = { vm.verify("wikidata-facts") }, onRemove = { confirmRemove = "wikidata-facts" })
                }
            }
            item { SectionLabel("Models", Modifier.padding(top = 12.dp)) }
            if (l == null || l.models.isEmpty()) {
                item { InfoNote("No model pack installed. Search still works; a model adds written answers with citations.") }
            }
            items(l?.models ?: emptyList(), key = { it.packId }) { m ->
                val loaded = (model as? ModelState.Loaded)?.kind == m.kind
                PackRow(
                    icon = Icons.Outlined.Memory,
                    title = m.title,
                    subtitle = when (m.kind) {
                        ModelKind.FAST -> "Fast model · default"
                        ModelKind.SMALL -> "Small model · for 8 GB phones"
                        ModelKind.DEEP -> "Deep model · Think harder"
                    } + if (loaded) " · loaded" else "",
                    detail = "${bytes(m.sizeBytes.toLong())} · ${m.license}",
                    onVerify = { vm.verify(m.packId) },
                    onRemove = { confirmRemove = m.packId },
                )
            }
            l?.skipped?.takeIf { it.isNotEmpty() }?.let { sk ->
                item { SectionLabel("Not loaded", Modifier.padding(top = 12.dp)) }
                items(sk) { s -> InfoNote("${s.packId}: ${s.reason}") }
            }
        }
    }

    detail?.let { p ->
        PackSheet(
            p,
            onDismiss = { detail = null },
            onDownload = { url ->
                try {
                    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                } catch (_: ActivityNotFoundException) {
                    vm.snackbar = "No browser app found on this phone"
                }
            },
            onInstall = { detail = null; picker.launch(arrayOf("*/*")) },
        )
    }
    confirmRemove?.let { id ->
        AlertDialog(
            onDismissRequest = { confirmRemove = null },
            title = { Text("Remove $id?") },
            text = { Text("This deletes the pack from the phone. You can import it again later.") },
            confirmButton = { TextButton(onClick = { vm.remove(id); confirmRemove = null }) { Text("Remove") } },
            dismissButton = { TextButton(onClick = { confirmRemove = null }) { Text("Cancel") } },
        )
    }
    if (vm.deletable.isNotEmpty()) {
        val size = vm.deletable.sumOf { it.second }
        AlertDialog(
            onDismissRequest = { vm.deletable = emptyList() },
            title = { Text("Delete the downloaded files?") },
            text = { Text("Installed and verified. The ${vm.deletable.size} downloaded files (${bytes(size)}) are no longer needed.") },
            confirmButton = { TextButton(onClick = vm::deleteSources, modifier = Modifier.testTag("delete_sources")) { Text("Delete") } },
            dismissButton = { TextButton(onClick = { vm.deletable = emptyList() }) { Text("Keep") } },
        )
    }
}

@Composable
private fun ImportProgress(s: ImportState) {
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.secondaryContainer, modifier = Modifier.fillMaxWidth().testTag("import_progress")) {
        Column(Modifier.padding(16.dp)) {
            Text(s.message, style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(10.dp))
            LinearProgressIndicator(progress = { (s.done.toDouble() / s.total).toFloat().coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(6.dp))
            Text("${bytes(s.done)} of ${bytes(s.total)} · checking SHA-256 as it copies", style = MaterialTheme.typography.labelSmall)
            for (p in s.finishedParts) {
                Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 4.dp)) {
                    Icon(Icons.Outlined.Verified, null, Modifier.size(14.dp), tint = LocalExtra.current.good)
                    Spacer(Modifier.width(6.dp))
                    Text(p, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@Composable
private fun PackRow(icon: ImageVector, title: String, subtitle: String, detail: String, onVerify: () -> Unit, onRemove: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.surfaceContainerLow, border = hairline(), modifier = Modifier.fillMaxWidth().testTag("pack_row")) {
        Row(Modifier.padding(start = 16.dp, top = 14.dp, bottom = 14.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, null, tint = MaterialTheme.colorScheme.primary)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(detail, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.outline)
            }
            IconButton(onClick = { menu = true }) { Icon(Icons.Outlined.MoreVert, "Actions") }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                DropdownMenuItem(text = { Text("Check integrity") }, onClick = { menu = false; onVerify() })
                DropdownMenuItem(text = { Text("Remove") }, onClick = { menu = false; onRemove() })
            }
        }
    }
}

