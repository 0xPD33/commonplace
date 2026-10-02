"""OpenStax textbooks from HuggingFaceTB/openstax_paragraphs (one JSON book per line).

One article per book module (a leaf chapter); section_path is "chapter > section".
Books that configs/openstax-books.tsv marks as NC are skipped. url_title is the book page on openstax.org.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .chunk import pack_blocks
from .packwrite import PackWriter

SKIP_RE = re.compile(r"question|exercise|problem|reference|bibliography|answer|homework|further research|"
                     r"check your understanding|test prep|additional resources|about the authors", re.I)
MD_RE = re.compile(r"(\*\*|\*|__)(\S.*?\S|\S)\1")
ESC_RE = re.compile(r"\\([$*_#%&{}])")
BOOKS_TSV = Path(__file__).resolve().parents[1] / "configs" / "openstax-books.tsv"


def clean(s: str | None) -> str:
    return "" if not s or s == "None" else ESC_RE.sub(r"\1", MD_RE.sub(r"\2", s)).strip()


def modules(chapters: list[dict], path: list[str]):
    """Yield (chapter path, module) for every chapter node that holds sections."""
    for ch in chapters:
        title = clean(ch.get("title"))
        if ch.get("sections") is not None or ch.get("abstract"):
            yield path, ch
        if ch.get("chapters"):
            yield from modules(ch["chapters"], path + [title])


def books() -> dict[str, tuple[str, str]]:
    rows = (l.split("\t") for l in BOOKS_TSV.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#"))
    return {title: (slug, lic) for title, slug, lic in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="openstax_books.jsonl")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    meta = books()
    w = PackWriter(args.out)
    with open(args.input, encoding="utf-8") as f:
        for line in f:
            book = json.loads(line)
            if book.get("language") != "en":
                continue
            slug, lic = meta[book["book_title"]]
            if "NC" in lic:
                continue
            btitle = clean(book["book_title"])
            for path, mod in modules(book["chapters"], []):
                mtitle = clean(mod.get("title"))
                if (path and path[0] == "Preface") or mtitle == "Preface":
                    continue
                chunks: list[tuple[str, str]] = []
                chapter = path[-1] if path else ""
                abstract = clean(mod.get("abstract"))
                if abstract and not abstract.startswith("By the end of this section"):
                    chunks += [(chapter, p) for p in pack_blocks([abstract])]
                for sec in mod.get("sections") or []:
                    stitle, para = clean(sec.get("title")), clean(sec.get("paragraph"))
                    if not para or SKIP_RE.search(stitle):
                        continue
                    blocks = [b.strip() for b in para.split("\n\n") if b.strip()]
                    sp = " > ".join(p for p in (chapter, stitle) if p)
                    chunks += [(sp, p) for p in pack_blocks(blocks)]
                w.add(f"{btitle}: {mtitle}", chunks, url_title=f"https://openstax.org/details/books/{slug}")
    w.close()


if __name__ == "__main__":
    main()
