"""Streaming writer for the packbuild input schema (articles.parquet + passages.parquet), shared by the extra packs."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .chunk import first_sentence

ARTICLE_COLS = ["article_id", "title", "qid", "popularity", "url_title", "oneliner", "first_passage", "n_passages"]
PASSAGE_COLS = ["passage_id", "article_id", "ordinal", "section_path", "text"]


class PackWriter:
    def __init__(self, out: str | Path):
        self.out = Path(out)
        self.out.mkdir(parents=True, exist_ok=True)
        self.arts: dict[str, list] = {k: [] for k in ARTICLE_COLS}
        self.buf: dict[str, list] = {k: [] for k in PASSAGE_COLS}
        self.pw: pq.ParquetWriter | None = None
        self.aid = self.pid = 0

    def add(self, title: str, chunks: list[tuple[str, str]], *, qid: str | None = None, popularity: int = 0,
            url_title: str | None = None, oneliner: str | None = None) -> None:
        """Add one article; chunks are (section_path, text) pairs in reading order."""
        if not chunks:
            return
        a = self.arts
        a["article_id"].append(self.aid)
        a["title"].append(title)
        a["qid"].append(qid or None)
        a["popularity"].append(max(0, int(popularity)))
        a["url_title"].append(url_title if url_title is not None else title.replace(" ", "_"))
        a["oneliner"].append(oneliner if oneliner is not None else first_sentence(chunks[0][1]))
        a["first_passage"].append(self.pid)
        a["n_passages"].append(len(chunks))
        for ordinal, (sec, text) in enumerate(chunks):
            self.buf["passage_id"].append(self.pid)
            self.buf["article_id"].append(self.aid)
            self.buf["ordinal"].append(min(ordinal, 65535))
            self.buf["section_path"].append(sec)
            self.buf["text"].append(text)
            self.pid += 1
        self.aid += 1
        if len(self.buf["passage_id"]) > 100_000:
            self._flush()

    def _flush(self) -> None:
        if not self.buf["passage_id"]:
            return
        t = pa.table({
            "passage_id": pa.array(self.buf["passage_id"], pa.uint32()),
            "article_id": pa.array(self.buf["article_id"], pa.uint32()),
            "ordinal": pa.array(self.buf["ordinal"], pa.uint16()),
            "section_path": pa.array(self.buf["section_path"], pa.string()),
            "text": pa.array(self.buf["text"], pa.string()),
        })
        if self.pw is None:
            self.pw = pq.ParquetWriter(self.out / "passages.parquet", t.schema, compression="zstd")
        self.pw.write_table(t)
        for v in self.buf.values():
            v.clear()

    def close(self) -> None:
        self._flush()
        if self.pw:
            self.pw.close()
        a = self.arts
        pq.write_table(pa.table({
            "article_id": pa.array(a["article_id"], pa.uint32()),
            "title": pa.array(a["title"], pa.string()),
            "qid": pa.array(a["qid"], pa.string()),
            "popularity": pa.array(a["popularity"], pa.uint64()),
            "url_title": pa.array(a["url_title"], pa.string()),
            "oneliner": pa.array(a["oneliner"], pa.string()),
            "first_passage": pa.array(a["first_passage"], pa.uint32()),
            "n_passages": pa.array(a["n_passages"], pa.uint32()),
        }), self.out / "articles.parquet", compression="zstd")
        print(f"articles={self.aid} passages={self.pid}")
