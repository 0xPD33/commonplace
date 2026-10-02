"""Stack Exchange threads from common-pile/stackexchange (2024-12 community dumps, per-document CC BY-SA).

Each source row is one thread as flat text: title line, question, question comments, then answers (accepted
first, then by votes), each followed by its comments. The rows carry no scores, accepted flags, or post
boundaries, so PLAN §6.2's score filter cannot be applied. Instead:
- thread filter: distinct authors >= a per-site threshold (a proxy for answers and attention);
- comment filter: drop segments that start with "@" or are very short;
- one article per thread, capped at the first MAX_PASSAGES passages (question and top answers come first).
"""

from __future__ import annotations

import argparse
import gzip
import json
from multiprocessing import Pool
from pathlib import Path

from .chunk import pack_blocks
from .packwrite import PackWriter

MAX_PASSAGES = 5
MIN_SEGMENT_CHARS = 40
DEFAULT_MIN_AUTHORS = 5
MIN_AUTHORS = {
    "math.stackexchange.com": 8,
    "diy.stackexchange.com": 3, "travel.stackexchange.com": 3, "cooking.stackexchange.com": 3,
    "mechanics.stackexchange.com": 3, "bicycles.stackexchange.com": 3, "outdoors.stackexchange.com": 3,
    **{f"{s}.stackexchange.com": 3 for s in (
        "gardening", "pets", "money", "law", "expatriates", "fitness", "parenting", "lifehacks", "woodworking",
        "homebrew", "coffee", "crafts", "sustainability", "health", "skeptics", "aviation", "space")},
}


def thread(doc: dict) -> tuple[str, list[tuple[str, str]], str, int] | None:
    meta = doc["metadata"]
    site = meta["site"]
    n_authors = sum(1 for a in meta.get("authors") or [] if a.startswith("https://"))
    if n_authors < MIN_AUTHORS.get(site, DEFAULT_MIN_AUTHORS) or not str(meta.get("license", "")).startswith("Creative Commons"):
        return None
    title, _, body = doc["text"].partition("\n")
    segs = [s.strip() for s in body.split("\n\n")]
    segs = [s for s in segs if len(s) >= MIN_SEGMENT_CHARS and not s.startswith("@")]
    passages = pack_blocks(segs)[:MAX_PASSAGES]
    if not title.strip() or not passages:
        return None
    return title.strip(), [(site, p) for p in passages], meta["url"].split("://", 1)[-1], n_authors


def read_file(path: str) -> list:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [t for line in f if (t := thread(json.loads(line)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="data/raw/stackexchange (one <site>/documents/*.jsonl.gz per site)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    files = sorted(str(p) for p in Path(args.input).glob("*/documents/*.jsonl.gz"))
    w = PackWriter(args.out)
    with Pool(args.workers) as pool:
        for path, threads in zip(files, pool.imap(read_file, files)):
            for title, chunks, url, n_authors in threads:
                w.add(title, chunks, popularity=n_authors, url_title=url)
            print(f"{path}: {len(threads)} threads", flush=True)
    w.close()


if __name__ == "__main__":
    main()
