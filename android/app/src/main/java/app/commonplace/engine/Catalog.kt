package app.commonplace.engine

import android.content.Context
import org.json.JSONObject

data class CatalogFile(val name: String, val bytes: Long, val url: String)

data class CatalogPack(
    val packId: String,
    val title: String,
    val description: String,
    val license: String,
    val replaces: List<String>,
    val recommended: Boolean,
    val downloadBytes: Long,
    val installedBytes: Long,
    val files: List<CatalogFile>,
)

/** The pack catalog bundled in the APK (assets/catalog.json, written by scripts/catalog.py). */
fun loadCatalog(ctx: Context): List<CatalogPack> = runCatching {
    val doc = JSONObject(ctx.assets.open("catalog.json").bufferedReader().use { it.readText() })
    val packs = doc.getJSONArray("packs")
    List(packs.length()) { i ->
        val p = packs.getJSONObject(i)
        val files = p.getJSONArray("files")
        val replaces = p.optJSONArray("replaces")
        CatalogPack(
            packId = p.getString("pack_id"),
            title = p.getString("title"),
            description = p.optString("description"),
            license = p.optString("license"),
            replaces = List(replaces?.length() ?: 0) { replaces!!.getString(it) },
            recommended = p.optBoolean("recommended"),
            downloadBytes = p.getLong("download_bytes"),
            installedBytes = p.getLong("installed_bytes"),
            files = List(files.length()) { j ->
                val f = files.getJSONObject(j)
                CatalogFile(f.getString("name"), f.getLong("bytes"), f.getString("url"))
            },
        )
    }
}.getOrDefault(emptyList())

/** Catalog packs that are neither installed nor replaced by an installed catalog pack. Starter packs first. */
fun availablePacks(catalog: List<CatalogPack>, installedIds: Set<String>): List<CatalogPack> {
    val replaced = catalog.filter { it.packId in installedIds }.flatMap { it.replaces }.toSet()
    return catalog.filter { it.packId !in installedIds && it.packId !in replaced }.sortedByDescending { it.recommended }
}

class SelectedFile<T>(val handle: T, val name: String, val size: Long)

class ImportJob<T>(val packId: String, val title: String, val files: List<SelectedFile<T>>)

class ImportPlan<T>(val jobs: List<ImportJob<T>>, val missing: List<String>)

private val PART = Regex("""^(.+)\.tar\.part\d+$""")

/**
 * Sort the selected files into packs. A catalog pack imports when every file is there by name and size.
 * Other `<id>.pack.json` files with `<id>.tar.partNNN` parts form a pack as well. Unrelated files are ignored.
 */
fun <T> planImport(catalog: List<CatalogPack>, selected: List<SelectedFile<T>>): ImportPlan<T> {
    val jobs = mutableListOf<ImportJob<T>>()
    val missing = mutableListOf<String>()
    val claimed = mutableSetOf<String>()
    for (pack in catalog) {
        val names = pack.files.map { it.name }.toSet()
        val have = selected.filter { it.name in names }
        if (have.isEmpty()) continue
        claimed += names
        val absent = pack.files.filter { f -> have.none { it.name == f.name && it.size == f.bytes } }
        if (absent.isEmpty()) {
            jobs += ImportJob(pack.packId, pack.title, have.distinctBy { it.name })
        } else {
            missing += "${pack.title}: " + absent.joinToString(", ") { f ->
                if (have.any { it.name == f.name }) "${f.name} (incomplete download)" else f.name
            }
        }
    }
    val groups = selected.filter { it.name !in claimed }.groupBy { f ->
        if (f.name.endsWith(".pack.json")) f.name.removeSuffix(".pack.json") else PART.matchEntire(f.name)?.groupValues?.get(1)
    }
    for ((id, files) in groups) {
        if (id == null) continue
        val hasIndex = files.any { it.name.endsWith(".pack.json") }
        val hasParts = files.any { !it.name.endsWith(".pack.json") }
        when {
            !hasIndex -> missing += "$id: $id.pack.json"
            !hasParts -> missing += "$id: the .tar.part files"
            else -> jobs += ImportJob(id, id, files)
        }
    }
    return ImportPlan(jobs, missing)
}
