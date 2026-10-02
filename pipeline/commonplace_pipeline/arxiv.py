"""arXiv titles and abstracts from librarian-bots/arxiv-metadata-snapshot (arXiv metadata, CC0).

One article per paper; section_path holds the categories.
"""

from __future__ import annotations

import argparse

import duckdb

from .chunk import pack_blocks
from .packwrite import PackWriter

MAX_AUTHORS_CHARS = 200


def one_line(s: str | None) -> str:
    return " ".join((s or "").split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="directory of arxiv-metadata-snapshot parquet files")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    con = duckdb.connect()
    con.execute("set enable_progress_bar=false")
    cur = con.execute(
        f"select id, title, authors, categories, abstract, versions[1].created from '{args.input}/*.parquet' "
        "where abstract is not null order by id")
    w = PackWriter(args.out)
    while rows := cur.fetchmany(50_000):
        for aid, title, authors, cats, abstract, created in rows:
            authors = one_line(authors)
            if len(authors) > MAX_AUTHORS_CHARS:
                authors = authors[:MAX_AUTHORS_CHARS].rsplit(",", 1)[0] + " et al."
            date = (created or "").split()  # "Tue, 6 Feb 2024 14:20:51 GMT"
            year = date[3] if len(date) > 3 else ""
            chunks = [(one_line(cats), p) for p in pack_blocks([one_line(abstract)])]
            if chunks:
                sec, last = chunks[-1]
                chunks[-1] = (sec, f"{last}\n{authors}. arXiv:{aid}" + (f" ({year})" if year else ""))
            w.add(one_line(title), chunks, url_title=aid)
    w.close()


if __name__ == "__main__":
    main()
