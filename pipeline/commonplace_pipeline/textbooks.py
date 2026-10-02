"""Open textbooks from common-pile: pressbooks_filtered and libretexts_filtered (CC BY, CC BY-SA, public
domain or GFDL per chapter; the chapter URL names the book and its authors) → packbuild input.

One article per chapter (Pressbooks) or section (LibreTexts), titled "<book>: <chapter>".

  python -m commonplace_pipeline.textbooks --input data/raw/common-pile/pressbooks-0000.json.gz \
    data/raw/common-pile/libretexts-0000.json.gz --out data/work/textbooks-en
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from urllib.parse import unquote, urlparse

from .chunk import chunk_article, words
from .packwrite import PackWriter

SKIP_RE = re.compile(r"(front|back) matter|title ?page|info ?page|table of contents|detailed licensing|licens|copyright|"
                     r"acknowledg|about the (author|book)|glossary|index\b|references|bibliography|versioning history", re.I)
MIN_WORDS = 80


def libretexts_book(url: str) -> str:
    segs = [unquote(s) for s in urlparse(url).path.split("/") if s]
    book = next((s for s in segs if s.startswith("Book:")), segs[2] if len(segs) > 2 else segs[-1])
    return book.removeprefix("Book:").replace("_", " ").strip(" -")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    w = PackWriter(a.out)
    for path in a.input:
        kept = 0
        for line in gzip.open(path, "rt"):
            d = json.loads(line)
            m, text = d["metadata"], d["text"]
            head = text.strip().split("\n", 1)[0].strip()
            if d.get("source") == "pressbooks":
                book, chapter = m.get("title", ""), head
            else:
                book, chapter = libretexts_book(d["id"]), m.get("title") or head
            if SKIP_RE.search(chapter) or words(text) < MIN_WORDS:
                continue
            title = f"{book}: {chapter}"[:300]
            w.add(title, chunk_article(title, text, None), popularity=len(text) // 1024, url_title=d["id"])
            kept += 1
        print(f"{path}: {kept} chapters")
    w.close()


if __name__ == "__main__":
    main()
