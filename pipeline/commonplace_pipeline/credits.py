"""CREDITS.txt for packs whose source rows carry their own authors and license.

  textbooks      one block per book (title, URL, authors, license) from the common-pile rows that `textbooks.py` kept
  stackexchange  one line per thread (URL, license versions, authors) from the rows that `stackexchange.py` kept

  python -m commonplace_pipeline.credits textbooks --input data/raw/common-pile/pressbooks-0000.json.gz \
    data/raw/common-pile/libretexts-0000.json.gz --out data/work/textbooks-en/CREDITS.txt
  python -m commonplace_pipeline.credits stackexchange --input data/raw/stackexchange --out data/work/stackexchange/CREDITS.txt
  zstd -19 --long=27 data/work/stackexchange/CREDITS.txt          # 270 MB -> 73 MB

Add the file to the pack with `packbuild notice --pack <dir> --credits <file>` (or `--credits` on `build`); a `.zst` file
is stored as `CREDITS.txt.zst`.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path
from urllib.parse import urlparse

from .chunk import words
from .stackexchange import thread
from .textbooks import MIN_WORDS, SKIP_RE, libretexts_book

LICENSE_URL_RE = re.compile(r"creativecommons\.org/(licenses|publicdomain)/([a-z-]+)/([0-9.]+)")
CC_NAMES = {"by": "CC BY", "by-sa": "CC BY-SA", "by-nc": "CC BY-NC", "by-nc-sa": "CC BY-NC-SA", "by-nd": "CC BY-ND",
            "by-nc-nd": "CC BY-NC-ND", "zero": "CC0", "mark": "Public Domain Mark"}


def license_name(raw: str) -> str:
    """'Creative Commons - Attribution - https://creativecommons.org/licenses/by/4.0/' -> 'CC BY 4.0'."""
    m = LICENSE_URL_RE.search(raw or "")
    if m:
        return f"{CC_NAMES.get(m[2], m[2].upper())} {m[3]}"
    return re.sub(r"\s*-?\s*https?://\S+", "", raw or "").strip() or "not stated"


def kept_chapters(path: str):
    """The rows `textbooks.py` keeps: (book key, book title, metadata)."""
    for line in gzip.open(path, "rt"):
        d = json.loads(line)
        m, text = d["metadata"], d["text"]
        head = text.strip().split("\n", 1)[0].strip()
        if d.get("source") == "pressbooks":
            book, chapter = m.get("title", ""), head
            u = urlparse(d["id"])
            key = (u.netloc, u.path.strip("/").split("/")[0])
        else:
            book, chapter = libretexts_book(d["id"]), m.get("title") or head
            key = (urlparse(d["id"]).netloc, book)
        if SKIP_RE.search(chapter) or words(text) < MIN_WORDS:
            continue
        yield d["source"], key, book, m


def textbooks(inputs: list[str], out: str) -> None:
    books: dict[tuple, dict] = defaultdict(lambda: {"urls": Counter(), "authors": Counter(), "licenses": Counter(),
                                                   "inst": Counter(), "n": 0})
    for path in inputs:
        for source, key, title, m in kept_chapters(path):
            b = books[(source, *key)]
            b["title"] = title
            b["n"] += 1
            b["urls"][m.get("book_url") or ""] += 1
            b["authors"][(m.get("author") or "").strip()] += 1
            b["licenses"][license_name(m.get("license", ""))] += 1
            b["inst"][(m.get("institution") or "").strip()] += 1
    lines = [
        "Credits for the books in this pack (Pressbooks and LibreTexts, via common-pile).",
        "Each article is one chapter or section of a book. The chapter URL (in the article source link) opens the original page.",
        "License is the license stated for the chapters in this pack; a book with several licenses lists all of them.",
        "",
    ]
    for (source, *_), b in sorted(books.items(), key=lambda kv: (kv[0][0], kv[1]["title"].lower())):
        authors = [a for a, _ in b["authors"].most_common() if a]
        url = next((u for u, _ in b["urls"].most_common() if u), "not stated")
        lines += [
            b["title"] or "(untitled)",
            f"  Source: {source}",
            f"  URL: {url}",
            f"  Authors: {'; '.join(authors) or 'not stated in the source data'}",
            f"  License: {', '.join(l for l, _ in b['licenses'].most_common())}",
            f"  Chapters in this pack: {b['n']}",
        ]
        inst = [i for i, _ in b["inst"].most_common() if i]
        if inst:
            lines.append(f"  Institution: {'; '.join(inst)}")
        lines.append("")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text("\n".join(lines), encoding="utf-8")
    lic = Counter(l for b in books.values() for l in b["licenses"])
    print(f"{len(books)} books, {sum(b['n'] for b in books.values())} chapters; licenses per book: {dict(lic)}")


PROFILE_RE = re.compile(r"^https://[^/]+/users/(\d+)")


def thread_line(doc: dict) -> str | None:
    if thread(doc) is None:
        return None
    m = doc["metadata"]
    licenses = [l for l in m.get("all_licenses") or [m.get("license", "")] if l.startswith("Creative Commons")] or [m["license"]]
    versions = sorted({license_name(l).removeprefix("CC BY-SA ") for l in licenses})
    authors = sorted({(f"u{p[1]}" if (p := PROFILE_RE.match(a)) else a) for a in m.get("authors") or []}, key=str.lower)
    return f"{m['url'].split('://', 1)[-1]}\t{','.join(versions)}\t{'; '.join(authors)}"


def se_file(path: str) -> list[str]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [s for line in f if (s := thread_line(json.loads(line)))]


def stackexchange(inp: str, out: str, workers: int) -> None:
    files = sorted(str(p) for p in Path(inp).glob("*/documents/*.jsonl.gz"))
    n = 0
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as w, Pool(workers) as pool:
        w.write(
            "Credits for the Stack Exchange threads in this pack.\n"
            "Each line is: thread URL, TAB, CC BY-SA version of the posts in the thread (2.5, 3.0 or 4.0), TAB, authors.\n"
            "An author written as u<number> is a Stack Exchange profile at https://<site>/users/<number> (same site as the thread).\n"
            "Contributors: names as shown on the thread page. The thread URL leads to the full, current authorship record.\n\n"
        )
        for lines in pool.imap(se_file, files):
            w.write("\n".join(lines) + ("\n" if lines else ""))
            n += len(lines)
    print(f"{n} threads")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("textbooks")
    t.add_argument("--input", required=True, nargs="+")
    t.add_argument("--out", required=True)
    s = sub.add_parser("stackexchange")
    s.add_argument("--input", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    if a.cmd == "textbooks":
        textbooks(a.input, a.out)
    else:
        stackexchange(a.input, a.out, a.workers)


if __name__ == "__main__":
    main()
