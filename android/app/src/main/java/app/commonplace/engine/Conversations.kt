package app.commonplace.engine

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import uniffi.commonplace_ffi.AnswerCard
import uniffi.commonplace_ffi.AnswerSegment
import uniffi.commonplace_ffi.FactItem
import uniffi.commonplace_ffi.FeaturedItem
import uniffi.commonplace_ffi.SourceItem
import uniffi.commonplace_ffi.TurnKind
import java.io.File

/** One finished turn as saved: enough to show it again and to use it as history for a follow-up. */
data class SavedTurn(
    val query: String,
    val deep: Boolean,
    val think: Boolean,
    val kind: TurnKind,
    val card: AnswerCard?,
    val sources: List<SourceItem>,
    val segments: List<AnswerSegment>,
)

/** Saved conversations, one JSON file each in `filesDir/conversations`. Nothing leaves the phone. */
class Conversations(context: Context) {
    private val dir = File(context.filesDir, "conversations")

    /** `text` holds every question and answer, for search. */
    data class Summary(val id: Long, val title: String, val updated: Long, val turns: Int, val text: String)

    fun save(id: Long, turns: List<SavedTurn>) {
        if (turns.isEmpty()) return
        dir.mkdirs()
        val json = JSONObject().put("id", id).put("updated", System.currentTimeMillis())
            .put("turns", JSONArray(turns.map { turn(it) }))
        // Write then rename, so a crash mid-write cannot leave a half file.
        val tmp = File(dir, "$id.json.tmp")
        tmp.writeText(json.toString())
        tmp.renameTo(File(dir, "$id.json"))
    }

    // ponytail: parses every file on each call; keep an index file if people save thousands.
    fun list(): List<Summary> = (dir.listFiles { f -> f.name.endsWith(".json") } ?: emptyArray())
        .mapNotNull { f ->
            runCatching {
                val o = JSONObject(f.readText())
                val turns = o.getJSONArray("turns").objects().map(::turn)
                Summary(
                    o.getLong("id"),
                    turns.first().query,
                    o.getLong("updated"),
                    turns.size,
                    turns.joinToString("\n") { t -> t.query + "\n" + t.segments.joinToString("") { text(it) } },
                )
            }.getOrNull()
        }
        .sortedByDescending { it.updated }

    fun load(id: Long): List<SavedTurn> =
        runCatching { JSONObject(File(dir, "$id.json").readText()).getJSONArray("turns").objects().map(::turn) }.getOrDefault(emptyList())

    fun delete(id: Long) {
        File(dir, "$id.json").delete()
    }
}

private fun JSONArray.objects(): List<JSONObject> = List(length()) { getJSONObject(it) }

private fun JSONArray.strings(): List<String> = List(length()) { getString(it) }

private fun text(s: AnswerSegment): String = when (s) {
    is AnswerSegment.Text -> s.text
    is AnswerSegment.Unverified -> s.text
    is AnswerSegment.Cite -> "[${s.n}]"
}

private fun turn(t: SavedTurn) = JSONObject()
    .put("query", t.query).put("deep", t.deep).put("think", t.think).put("kind", t.kind.name)
    .put("card", t.card?.let(::card) ?: JSONObject.NULL)
    .put("sources", JSONArray(t.sources.map(::source)))
    .put("segments", JSONArray(t.segments.map { s ->
        when (s) {
            is AnswerSegment.Text -> JSONObject().put("text", s.text)
            is AnswerSegment.Unverified -> JSONObject().put("unverified", s.text)
            is AnswerSegment.Cite -> JSONObject().put("cite", s.n.toLong())
        }
    }))

private fun turn(o: JSONObject) = SavedTurn(
    o.getString("query"),
    o.getBoolean("deep"),
    o.getBoolean("think"),
    TurnKind.valueOf(o.getString("kind")),
    o.optJSONObject("card")?.let(::card),
    o.getJSONArray("sources").objects().map(::source),
    o.getJSONArray("segments").objects().map { s ->
        when {
            s.has("cite") -> AnswerSegment.Cite(s.getLong("cite").toUInt())
            s.has("unverified") -> AnswerSegment.Unverified(s.getString("unverified"))
            else -> AnswerSegment.Text(s.getString("text"))
        }
    },
)

private fun source(s: SourceItem) = JSONObject()
    .put("n", s.n.toLong()).put("pack", s.packId).put("packTitle", s.packTitle).put("pid", s.passageId.toLong())
    .put("aid", s.articleId.toLong()).put("title", s.title).put("section", s.section).put("snippet", s.snippet)
    .put("license", s.license).put("url", s.sourceUrl)

private fun source(o: JSONObject) = SourceItem(
    o.getLong("n").toUInt(), o.getString("pack"), o.getString("packTitle"), o.getLong("pid").toUInt(),
    o.getLong("aid").toUInt(), o.getString("title"), o.getString("section"), o.getString("snippet"),
    o.optString("license"), o.optString("url"),
)

private fun card(c: AnswerCard) = JSONObject()
    .put("top", c.top?.let(::source) ?: JSONObject.NULL).put("topText", c.topText).put("highlight", c.highlight)
    .put("fromCards", c.fromCards)
    .put("facts", JSONArray(c.facts.map { f -> JSONObject().put("entity", f.entity).put("label", f.label).put("value", f.value).put("when", f.`when` ?: JSONObject.NULL) }))
    .put("computed", JSONArray(c.computed)).put("sources", JSONArray(c.sources.map(::source))).put("entities", JSONArray(c.entities))
    .put("cardMs", c.cardMs)
    .put("featured", c.featured?.let { f -> JSONObject().put("text", f.text).put("sentence", f.sentence).put("source", source(f.source)) } ?: JSONObject.NULL)

private fun card(o: JSONObject) = AnswerCard(
    o.optJSONObject("top")?.let(::source),
    o.getString("topText"),
    o.getString("highlight"),
    o.getBoolean("fromCards"),
    o.getJSONArray("facts").objects().map { f -> FactItem(f.getString("entity"), f.getString("label"), f.getString("value"), if (f.isNull("when")) null else f.getString("when")) },
    o.getJSONArray("computed").strings(),
    o.getJSONArray("sources").objects().map(::source),
    o.getJSONArray("entities").strings(),
    o.getDouble("cardMs"),
    o.optJSONObject("featured")?.let { f -> FeaturedItem(f.getString("text"), f.getString("sentence"), source(f.getJSONObject("source"))) },
)
