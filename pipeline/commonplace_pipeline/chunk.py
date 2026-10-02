"""Heading-aware chunker for finewiki-style markdown articles.

Input: a finewiki parquet (columns: title, text, url, wikidata_id, infoboxes, bytes_html).
Output (in --out): articles.parquet and passages.parquet in the packbuild input schema (see PACKS.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

MIN_WORDS, MAX_WORDS, MERGE_MAX = 80, 220, 260
MAX_TABLE_ROWS = 40
SKIP_SECTIONS = {
    "see also", "references", "external links", "further reading", "notes", "related pages",
    "other websites", "sources", "bibliography", "citations", "footnotes", "gallery", "notes and references",
}
SENT_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[]?[A-Z0-9])")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def words(s: str) -> int:
    return len(s.split())


def split_sentences(text: str) -> list[str]:
    return [s for s in SENT_RE.split(text) if s.strip()]


def linearize_table(lines: list[str]) -> str | None:
    rows = [[c.strip() for c in ln.strip().strip("|").split("|")] for ln in lines]
    rows = [r for r in rows if not all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in r)]
    if len(rows) < 2:
        return None
    header, body = rows[0], rows[1:]
    if len(body) > MAX_TABLE_ROWS:
        return None
    out = []
    for r in body:
        cells = [f"{h}: {v}" if h else v for h, v in zip(header, r) if v]
        if cells:
            out.append("; ".join(cells))
    return "\n".join(out) or None


@dataclass
class Section:
    path: list[str]
    blocks: list[str] = field(default_factory=list)


def parse_sections(md: str) -> list[Section]:
    sections = [Section(path=[])]
    stack: list[tuple[int, str]] = []
    lines = md.splitlines()
    i = 0
    para: list[str] = []

    def flush_para():
        if para:
            sections[-1].blocks.append(" ".join(para).strip())
            para.clear()

    while i < len(lines):
        ln = lines[i]
        m = HEADING_RE.match(ln)
        if m:
            flush_para()
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:  # article title
                i += 1
                continue
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            sections.append(Section(path=[t for _, t in stack]))
        elif ln.lstrip().startswith("|"):
            flush_para()
            table = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                table.append(lines[i])
                i += 1
            lin = linearize_table(table)
            if lin:
                sections[-1].blocks.append(lin)
            continue
        elif re.match(r"^\s*([-*]|\d+\.)\s+", ln):
            flush_para()
            items = []
            while i < len(lines) and re.match(r"^\s*([-*]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[i]).strip())
                i += 1
            sections[-1].blocks.append("\n".join(items))
            continue
        elif not ln.strip():
            flush_para()
        else:
            para.append(ln.strip())
        i += 1
    flush_para()
    return sections


def pack_blocks(blocks: list[str]) -> list[str]:
    """Paragraph-pack blocks into 80-220 word passages; split long blocks at sentences."""
    units: list[str] = []
    for b in blocks:
        if words(b) <= MAX_WORDS:
            units.append(b)
            continue
        cur: list[str] = []
        for s in split_sentences(b) if "\n" not in b else b.split("\n"):
            if cur and words(" ".join(cur + [s])) > MAX_WORDS:
                units.append(" ".join(cur))
                cur = []
            cur.append(s)
        if cur:
            units.append(" ".join(cur))
    out: list[str] = []
    cur = ""
    for u in units:
        if not cur:
            cur = u
        elif words(cur) < MIN_WORDS and words(cur) + words(u) <= MAX_WORDS:
            cur = cur + "\n" + u
        else:
            out.append(cur)
            cur = u
    if cur:
        if out and words(cur) < MIN_WORDS and words(out[-1]) + words(cur) <= MERGE_MAX:
            out[-1] = out[-1] + "\n" + cur
        else:
            out.append(cur)
    return out


def infobox_passage(infoboxes: str | None) -> str | None:
    if not infoboxes:
        return None
    try:
        boxes = json.loads(infoboxes)
    except json.JSONDecodeError:
        return None
    if not boxes or not isinstance(boxes[0], dict):
        return None
    data = boxes[0].get("data") or {}
    lines = [f"{k}: {v}" for k, v in data.items() if isinstance(v, str) and v.strip() and len(v) < 300]
    return "\n".join(lines[:30]) or None


def is_skipped_article(title: str, text: str) -> bool:
    return "(disambiguation)" in title or title.startswith("List of") or "may refer to" in text[:300]


def first_sentence(text: str) -> str:
    s = split_sentences(text.replace("\n", " "))
    return (s[0] if s else text)[:300]


def chunk_article(title: str, md: str, infoboxes: str | None) -> list[tuple[str, str]]:
    """Return (section_path, passage_text) pairs in reading order."""
    out: list[tuple[str, str]] = []
    for sec in parse_sections(md):
        if any(p.lower() in SKIP_SECTIONS for p in sec.path):
            continue
        blocks = [b for b in sec.blocks if b]
        for p in pack_blocks(blocks):
            out.append((" > ".join(sec.path), p))
    ib = infobox_passage(infoboxes)
    if ib:
        out.append(("Infobox", ib))
    return out


def pageviews(db_path: str, titles: list[str]) -> list[int]:
    """Views per title. Pageview titles are lowercase with underscores; `pages` has no index, so join once."""
    keys = [t.replace(" ", "_").lower() for t in titles]
    db = sqlite3.connect(db_path)
    db.execute("CREATE TEMP TABLE need(title TEXT PRIMARY KEY)")
    db.executemany("INSERT OR IGNORE INTO need VALUES (?)", ((k,) for k in keys))
    views = dict(db.execute("SELECT p.title, SUM(p.views) FROM pages p JOIN need n ON n.title = p.title GROUP BY p.title"))
    print(f"pageviews: {sum(k in views for k in keys)} of {len(keys)} titles matched")
    return [int(views.get(k, 0)) for k in keys]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, nargs="+", help="finewiki parquet file(s)")
    ap.add_argument("--pageviews", help="pageviews.sqlite with pages(title, views); without it, popularity is the HTML size")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="max articles (0 = all)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cols = ["title", "text", "url", "wikidata_id", "infoboxes", "bytes_html"]
    batches = (b for f in args.input for b in pq.ParquetFile(f).iter_batches(batch_size=2048, columns=cols))
    arts: dict[str, list] = {k: [] for k in ["article_id", "title", "qid", "popularity", "url_title", "oneliner", "first_passage", "n_passages"]}
    pw = None
    pid = aid = 0
    buf: dict[str, list] = {k: [] for k in ["passage_id", "article_id", "ordinal", "section_path", "text"]}

    def flush():
        nonlocal pw
        if not buf["passage_id"]:
            return
        t = pa.table({
            "passage_id": pa.array(buf["passage_id"], pa.uint32()),
            "article_id": pa.array(buf["article_id"], pa.uint32()),
            "ordinal": pa.array(buf["ordinal"], pa.uint16()),
            "section_path": pa.array(buf["section_path"], pa.string()),
            "text": pa.array(buf["text"], pa.string()),
        })
        if pw is None:
            pw = pq.ParquetWriter(out / "passages.parquet", t.schema, compression="zstd")
        pw.write_table(t)
        for v in buf.values():
            v.clear()

    done = 0
    for batch in batches:
        for r in batch.to_pylist():
            title, text = r["title"], r["text"] or ""
            if is_skipped_article(title, text):
                continue
            chunks = chunk_article(title, text, r["infoboxes"])
            if not chunks:
                continue
            arts["article_id"].append(aid)
            arts["title"].append(title)
            arts["qid"].append(r["wikidata_id"] or None)
            # ponytail: HTML size is the popularity proxy when no pageviews are given (simplewiki).
            arts["popularity"].append(int(r["bytes_html"] or 0) // 1000)
            arts["url_title"].append(r["url"].rsplit("/", 1)[-1])
            arts["oneliner"].append(first_sentence(chunks[0][1]))
            arts["first_passage"].append(pid)
            arts["n_passages"].append(len(chunks))
            for ordinal, (sec, text_) in enumerate(chunks):
                buf["passage_id"].append(pid)
                buf["article_id"].append(aid)
                buf["ordinal"].append(min(ordinal, 65535))
                buf["section_path"].append(sec)
                buf["text"].append(text_)
                pid += 1
            aid += 1
            done += 1
            if args.limit and done >= args.limit:
                break
        if len(buf["passage_id"]) > 100_000:
            flush()
        if args.limit and done >= args.limit:
            break
    flush()
    if pw:
        pw.close()
    if args.pageviews:
        arts["popularity"] = pageviews(args.pageviews, arts["title"])
    pq.write_table(pa.table({
        "article_id": pa.array(arts["article_id"], pa.uint32()),
        "title": pa.array(arts["title"], pa.string()),
        "qid": pa.array(arts["qid"], pa.string()),
        "popularity": pa.array(arts["popularity"], pa.uint64()),
        "url_title": pa.array(arts["url_title"], pa.string()),
        "oneliner": pa.array(arts["oneliner"], pa.string()),
        "first_passage": pa.array(arts["first_passage"], pa.uint32()),
        "n_passages": pa.array(arts["n_passages"], pa.uint32()),
    }), out / "articles.parquet", compression="zstd")
    print(f"articles={aid} passages={pid}")


if __name__ == "__main__":
    main()
