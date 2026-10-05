package app.commonplace.engine

import android.content.Context
import org.json.JSONObject

data class CatalogFile(val name: String, val bytes: Long, val sha256: String, val url: String)

/**
 * One download of the catalog: a pack, or a bundle (one file with several packs).
 * For a bundle, `packId` is the bundle id and `members` lists the pack ids inside its file, with their titles in
 * `memberTitles`. `members` is empty for a pack.
 */
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
    val members: List<String> = emptyList(),
    val memberTitles: Map<String, String> = emptyMap(),
)

class Catalog(val packs: List<CatalogPack>, val bundles: List<CatalogPack>)

/** The catalog bundled in the APK (assets/catalog.json, written by scripts/catalog.py). It may have no `bundles`. */
fun loadCatalog(ctx: Context): Catalog = runCatching {
    val doc = JSONObject(ctx.assets.open("catalog.json").bufferedReader().use { it.readText() })
    fun entries(key: String, idKey: String) = doc.optJSONArray(key)?.let { list ->
        List(list.length()) { i ->
            val p = list.getJSONObject(i)
            val files = p.getJSONArray("files")
            val replaces = p.optJSONArray("replaces")
            val members = p.optJSONArray("pack_ids")
            val titled = p.optJSONArray("members")
            CatalogPack(
                packId = p.getString(idKey),
                title = p.getString("title"),
                description = p.optString("description"),
                license = p.optString("license"),
                replaces = List(replaces?.length() ?: 0) { replaces!!.getString(it) },
                recommended = p.optBoolean("recommended"),
                downloadBytes = p.getLong("download_bytes"),
                installedBytes = p.getLong("installed_bytes"),
                files = List(files.length()) { j ->
                    val f = files.getJSONObject(j)
                    CatalogFile(f.getString("name"), f.getLong("bytes"), f.getString("sha256"), f.getString("url"))
                },
                members = List(members?.length() ?: 0) { members!!.getString(it) },
                memberTitles = List(titled?.length() ?: 0) { titled!!.getJSONObject(it) }.associate { it.getString("pack_id") to it.getString("title") },
            )
        }
    }.orEmpty()
    Catalog(entries("packs", "pack_id"), entries("bundles", "bundle_id"))
}.getOrDefault(Catalog(emptyList(), emptyList()))

/** The installed pack ids, plus the packs that an installed catalog pack replaces. */
private fun present(catalog: Catalog, installedIds: Set<String>) =
    installedIds + catalog.packs.filter { it.packId in installedIds }.flatMap { it.replaces }

/** Catalog packs that are neither installed nor replaced by an installed catalog pack. Starter packs first. */
fun availablePacks(catalog: Catalog, installedIds: Set<String>): List<CatalogPack> {
    val have = present(catalog, installedIds)
    return catalog.packs.filter { it.packId !in have }.sortedByDescending { it.recommended }
}

/** Bundles that have a pack that is not installed yet, each with the number of its packs that are installed. */
fun availableBundles(catalog: Catalog, installedIds: Set<String>): List<Pair<CatalogPack, Int>> {
    val have = present(catalog, installedIds)
    return catalog.bundles.map { b -> b to b.members.count { it in have } }.filter { (b, n) -> n < b.members.size }
}

class SelectedFile<T>(val handle: T, val name: String, val size: Long)

/**
 * `sha256` maps a file name to the hash from the catalog; the core checks each file against it.
 * `packIds` are the packs that the files install: several for a bundle.
 */
class ImportJob<T>(
    val packId: String,
    val title: String,
    val files: List<SelectedFile<T>>,
    val sha256: Map<String, String> = emptyMap(),
    val packIds: List<String> = listOf(packId),
)

class ImportPlan<T>(val jobs: List<ImportJob<T>>, val missing: List<String>)

private val PART = Regex("""^(.+)\.tar(?:\.part\d+)?$""")

/**
 * Sort the selected files into packs. A catalog bundle or pack imports when every file is there by name and size.
 * Other `<id>.tar` files import alone, and `<id>.pack.json` with `<id>.tar.partNNN` parts form a pack as well.
 * Unrelated files are ignored.
 */
fun <T> planImport(catalog: Catalog, selected: List<SelectedFile<T>>): ImportPlan<T> {
    val jobs = mutableListOf<ImportJob<T>>()
    val missing = mutableListOf<String>()
    val claimed = mutableSetOf<String>()
    for (pack in catalog.bundles + catalog.packs) {
        val names = pack.files.map { it.name }.toSet()
        val have = selected.filter { it.name in names }
        if (have.isEmpty()) continue
        claimed += names
        val absent = pack.files.filter { f -> have.none { it.name == f.name && it.size == f.bytes } }
        if (absent.isEmpty()) {
            jobs += ImportJob(
                pack.packId, pack.title, have.distinctBy { it.name }, pack.files.associate { it.name to it.sha256 },
                pack.members.ifEmpty { listOf(pack.packId) },
            )
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
            files.size == 1 && files[0].name == "$id.tar" -> jobs += ImportJob(id, id, files)
            !hasIndex -> missing += "$id: $id.pack.json"
            !hasParts -> missing += "$id: the .tar.part files"
            else -> jobs += ImportJob(id, id, files)
        }
    }
    return ImportPlan(jobs, missing)
}
