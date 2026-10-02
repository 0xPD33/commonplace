"""Kiwix ZIM files (MediaWiki or DevDocs) → packbuild input (articles.parquet, passages.parquet).

The HTML becomes heading-marked text for chunk.py: MediaWiki pages keep only `.mw-parser-output`;
references, navboxes, edit links and figures are dropped. Popularity is the page's HTML size in KB
(ZIMs carry no pageviews), the same stand-in as the simplewiki dev pack.

  python -m commonplace_pipeline.zim --input data/raw/zim/wikibooks_en_all_nopic_2026-04.zim --out data/work/wikibooks-en
  python -m commonplace_pipeline.zim --input data/raw/zim/devdocs_en_*.zim --prefix --out data/work/devdocs-en
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import lxml.html
from libzim.reader import Archive

from .chunk import chunk_article, words
from .packwrite import PackWriter

DROP = (
    "script", "style", "figure", "sup.reference", ".mw-editsection", ".navbox", ".vertical-navbox", ".reflist",
    ".references", ".thumb", ".noprint", ".metadata", ".ambox", ".hatnote", ".toc", "#toc", ".mw-empty-elt",
    ".printfooter", ".catlinks", "table.infobox", ".gallery",
)
RECURSE = {"div", "section", "blockquote", "dl", "details", "article", "main", "center", "span"}
SKIP_TITLE_RE = re.compile(r"^(File|Category|Template|Help|Special|Wikibooks|Wikiquote|Wikiversity|Portal|Talk|User)( talk)?:")
# Translate-extension subpages ("Sepsis/ru"). WikEM marks its machine translations as English; Wikibooks has
# real English pages that end the same way ("Perl Programming/Keywords/no"), hence a flag.
TRANSLATION_RE = re.compile(r"/(ar|bg|bn|ca|cs|da|de|el|es|et|fa|fi|fr|he|hi|hr|hu|id|it|ja|ko|lt|lv|ms|nb|nl|no|pl|pt|pt-br|"
                            r"ro|ru|sk|sl|sr|sv|sw|ta|te|th|tr|uk|ur|vi|zh|zh-hans|zh-hant)$")
MIN_ARTICLE_WORDS = 40
WS = re.compile(r"\s+")


def clean(s: str) -> str:
    return WS.sub(" ", s).strip()


def table_lines(t) -> list[str]:
    rows = []
    for tr in t.iter("tr"):
        cells = [clean(c.text_content()).replace("|", "/") for c in tr if c.tag in ("td", "th")]
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")
    return rows


def to_markdown(el, out: list[str]) -> None:
    for c in el:
        if not isinstance(c.tag, str):
            continue
        tag = c.tag.lower()
        if re.fullmatch(r"h[1-6]", tag):
            out += ["", "#" * int(tag[1]) + " " + clean(c.text_content()), ""]
        elif tag in ("p", "pre", "dd", "dt"):
            t = clean(c.text_content())
            if t:
                out += [t, ""]
        elif tag in ("ul", "ol"):
            items = [clean(li.text_content()) for li in c if isinstance(li.tag, str) and li.tag == "li"]
            out += [f"- {t}" for t in items if t] + [""]
        elif tag == "table":
            out += table_lines(c) + [""]
        elif tag in RECURSE:
            to_markdown(c, out)


def page_markdown(html: bytes, root: str | None = None) -> str:
    doc = lxml.html.fromstring(html)
    for sel in DROP:
        for e in doc.cssselect(sel):
            e.drop_tree()
    roots = ((root and doc.cssselect(root)) or doc.cssselect(".mw-parser-output") or doc.cssselect("#mw-content-text")
             or doc.cssselect("body") or [doc])
    if not roots[0].get("lang", "en").startswith("en"):  # ArchWiki ships its translations in the English ZIM
        return ""
    out: list[str] = []
    for r in roots:
        to_markdown(r, out)
    return "\n".join(out)


def prefix_of(zim: Path) -> str:
    """devdocs_en_python_2026-08.zim → "Python"."""
    return zim.stem.split("_")[2].replace("-", " ").title()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefix", action="store_true", help="prefix titles with the docset name (DevDocs)")
    ap.add_argument("--drop-translations", action="store_true", help="skip language-code subpages (WikEM)")
    a = ap.parse_args()

    w = PackWriter(a.out)
    for path in map(Path, a.input):
        zim = Archive(path)
        pre = f"{prefix_of(path)}: " if a.prefix else ""
        kept = 0
        for i in range(zim.all_entry_count):
            e = zim._get_entry_by_id(i)
            if e.is_redirect or SKIP_TITLE_RE.match(e.title) or (a.drop_translations and TRANSLATION_RE.search(e.title)):
                continue
            item = e.get_item()
            if not item.mimetype.startswith("text/html"):
                continue
            html = bytes(item.content)
            try:
                md = page_markdown(html)
            except (ValueError, lxml.etree.ParserError):
                continue
            chunks = chunk_article(e.title, md, None)
            if sum(words(t) for _, t in chunks) < MIN_ARTICLE_WORDS:
                continue
            w.add(pre + e.title, chunks, popularity=len(html) // 1024, url_title=e.path)
            kept += 1
        print(f"{path.name}: {kept} articles")
    w.close()


if __name__ == "__main__":
    main()
