package app.commonplace.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.BaselineShift
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import uniffi.commonplace_ffi.AnswerSegment
import java.util.BitSet

/** One line of a rendered answer. [marker] is "•" or "3." for list items, empty otherwise. */
data class AnswerBlock(val kind: Kind, val text: AnnotatedString, val marker: String = "", val level: Int = 0, val gapBefore: Boolean = false) {
    enum class Kind { Paragraph, Heading, Item }
}

/** Colors the renderer needs from the theme. */
data class AnswerColors(
    val cite: Color,
    val unverified: Color,
    val codeBackground: Color,
)

// Each citation becomes one private-use character, so Markdown parsing never splits or reorders it.
private const val CITE_BASE = 0xE000
private val HEADING = Regex("""^#{1,6}\s+""")
private val BULLET = Regex("""^[-*+•]\s+""")
private val NUMBERED = Regex("""^(\d{1,3})[.)]\s+""")
private val RULE = Regex("""^(-{3,}|\*{3,}|_{3,})$""")
private val MARKS = Regex("""\*\*|[*`]|(?m)^\s*(#{1,6}|[-+•]|\d{1,3}[.)])\s+""")

/**
 * The Markdown subset small models write (headings, bullet and numbered lists, **bold**, *italic*,
 * `code`) over the checked answer segments. Citations stay tappable and unverified sentences stay
 * underlined. An unclosed marker styles the rest of its line, so half-streamed text looks right.
 */
fun answerBlocks(
    segments: List<AnswerSegment>,
    colors: AnswerColors,
    cursor: Boolean,
    onCite: (n: UInt, sentence: String) -> Unit,
): List<AnswerBlock> {
    val src = StringBuilder()
    val unverified = BitSet()
    val cites = ArrayList<Pair<UInt, String>>()
    var lastSentence = ""
    for (s in segments) when (s) {
        is AnswerSegment.Text -> { src.append(s.text); lastSentence = s.text }
        is AnswerSegment.Unverified -> {
            unverified.set(src.length, src.length + s.text.length)
            src.append(s.text)
            lastSentence = s.text
        }
        is AnswerSegment.Cite -> {
            src.append((CITE_BASE + cites.size).toChar())
            cites += s.n to lastSentence.replace(MARKS, "").trim()
        }
    }

    val blocks = ArrayList<AnswerBlock>()
    var blank = false
    var start = 0
    while (start <= src.length) {
        val end = src.indexOf("\n", start).let { if (it < 0) src.length else it }
        val line = src.substring(start, end)
        val body = line.trimStart()
        val indent = line.length - body.length
        val from = start + indent
        start = end + 1
        if (body.isBlank() || RULE.matches(body.trimEnd())) {
            blank = blocks.isNotEmpty()
            continue
        }
        val heading = HEADING.find(body)
        val bullet = BULLET.find(body)
        val numbered = NUMBERED.find(body)
        val (kind, marker, skip) = when {
            heading != null -> Triple(AnswerBlock.Kind.Heading, "", heading.value.length)
            bullet != null -> Triple(AnswerBlock.Kind.Item, "•", bullet.value.length)
            numbered != null -> Triple(AnswerBlock.Kind.Item, "${numbered.groupValues[1]}.", numbered.value.length)
            else -> Triple(AnswerBlock.Kind.Paragraph, "", 0)
        }
        val text = inline(src, from + skip, from + body.length, unverified, cites, colors, onCite)
        blocks += AnswerBlock(kind, text, marker, level = if (kind == AnswerBlock.Kind.Item && indent >= 2) 1 else 0, gapBefore = blank)
        blank = false
    }
    if (cursor) {
        val caret = buildAnnotatedString { withStyle(SpanStyle(color = colors.cite)) { append(" ▍") } }
        val last = blocks.lastOrNull()
        if (last == null) blocks += AnswerBlock(AnswerBlock.Kind.Paragraph, caret) else blocks[blocks.lastIndex] = last.copy(text = last.text + caret)
    }
    return blocks
}

private fun inline(
    src: CharSequence,
    from: Int,
    to: Int,
    unverified: BitSet,
    cites: List<Pair<UInt, String>>,
    colors: AnswerColors,
    onCite: (UInt, String) -> Unit,
): AnnotatedString = buildAnnotatedString {
    var bold = false
    var italic = false
    var code = false
    var runUnverified = false
    var prevCite = false
    val run = StringBuilder()
    fun flush() {
        if (run.isEmpty()) return
        val style = SpanStyle(
            fontWeight = if (bold) FontWeight.SemiBold else null,
            fontStyle = if (italic) FontStyle.Italic else null,
            fontFamily = if (code) FontFamily.Monospace else null,
            fontSize = if (code) 0.9.em else TextUnit.Unspecified,
            letterSpacing = if (code) 0.sp else TextUnit.Unspecified,
            background = if (code) colors.codeBackground else Color.Unspecified,
            textDecoration = if (runUnverified) TextDecoration.Underline else null,
            color = if (runUnverified) colors.unverified else Color.Unspecified,
        )
        withStyle(style) { append(run) }
        run.clear()
    }
    var i = from
    while (i < to) {
        val ch = src[i]
        val cite = ch.code - CITE_BASE
        when {
            cite in cites.indices -> {
                flush()
                val (n, sentence) = cites[cite]
                val sup = SpanStyle(baselineShift = BaselineShift(0.35f), fontSize = 0.76.em, color = colors.cite)
                if (prevCite) withStyle(sup) { append(",") }
                val link = LinkAnnotation.Clickable("cite-$n", TextLinkStyles(SpanStyle(color = colors.cite, fontWeight = FontWeight.Bold))) {
                    onCite(n, sentence)
                }
                withLink(link) { withStyle(sup) { append(if (prevCite) "$n" else " $n") } }
                prevCite = true
                i++
                continue
            }
            ch == '`' -> { flush(); code = !code; i++; continue }
            !code && ch == '*' && i + 1 < to && src[i + 1] == '*' -> { flush(); bold = !bold; i += 2; continue }
            !code && ch == '*' && italicMark(src, i, from, to, italic) -> { flush(); italic = !italic; i++; continue }
        }
        val u = unverified[i]
        if (u != runUnverified) { flush(); runUnverified = u }
        run.append(ch)
        prevCite = false
        i++
    }
    flush()
}

/** `*` opens italics before a word with a closing `*` later on the line, and closes after a word. */
private fun italicMark(src: CharSequence, i: Int, from: Int, to: Int, open: Boolean): Boolean =
    if (open) i > from && !src[i - 1].isWhitespace()
    else i + 1 < to && !src[i + 1].isWhitespace() && (i + 2 until to).any { src[it] == '*' && !src[it - 1].isWhitespace() }
