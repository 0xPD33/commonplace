"""Embed a work directory's passages with mxbai-embed-large-v1 on a GPU and write packbuild dense input.

Input:  <work>/articles.parquet, <work>/passages.parquet (chunk.py schema).
Output: <work>/dense/{codes.u8,assign.u32,centroids.f32,info.json}, the same files that plan_a.py writes.

The document text is "{title}\n{text}" (plus " > {section}" after the title when the passage has one),
CLS pooling, no prompt, fp16. A code is the sign of the first 512 dimensions, packed like numpy.packbits.
Shards of --shard passages go to <work>/dense/shards/; a restart skips finished shards.
dense/hashes.u64 holds a 64-bit hash of each embedded text. `--reuse <last month's work dir>` copies the codes of
unchanged texts, embeds only new or changed ones, and keeps that dir's IVF centroids.

  uv run --project pipeline --extra gpu python -m commonplace_pipeline.embed --work data/work/openstax
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from .plan_a import CODE_BYTES, DIMS, assign_all, train_ivf

MODEL = "mixedbread-ai/mxbai-embed-large-v1"


class Encoder:
    def __init__(self, model: str, max_len: int, batch_tokens: int):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model)
        self.model = AutoModel.from_pretrained(model, dtype=torch.float16, attn_implementation="sdpa").cuda().eval()
        self.max_len = max_len
        self.batch_tokens = batch_tokens

    def codes(self, texts: list[str]) -> np.ndarray:
        torch = self.torch
        ids = self.tok(texts, truncation=True, max_length=self.max_len, add_special_tokens=True)["input_ids"]
        order = np.argsort([len(x) for x in ids])[::-1]  # longest first: an OOM shows up in the first batch
        out = np.empty((len(texts), CODE_BYTES), dtype=np.uint8)
        pad = self.tok.pad_token_id
        i = 0
        with torch.inference_mode():
            while i < len(order):
                width = len(ids[order[i]])
                n = max(1, self.batch_tokens // width)
                rows = order[i : i + n]
                x = torch.full((len(rows), width), pad, dtype=torch.long)
                m = torch.zeros((len(rows), width), dtype=torch.long)
                for r, k in enumerate(rows):
                    x[r, : len(ids[k])] = torch.tensor(ids[k])
                    m[r, : len(ids[k])] = 1
                h = self.model(input_ids=x.cuda(), attention_mask=m.cuda()).last_hidden_state[:, 0, :DIMS]
                out[rows] = np.packbits((h > 0).cpu().numpy(), axis=1)
                i += n
        return out


def doc_text(title: str, section: str | None, text: str) -> str:
    return f"{title} > {section}\n{text}" if section else f"{title}\n{text}"


def text_hashes(texts: list[str]) -> np.ndarray:
    return np.array([int.from_bytes(hashlib.blake2b(t.encode(), digest_size=8).digest(), "little") for t in texts], dtype=np.uint64)


class Reuse:
    """Codes of a previous work dir, looked up by text hash."""

    def __init__(self, prev: Path):
        h = np.fromfile(prev / "dense/hashes.u64", dtype=np.uint64)
        self.codes = np.fromfile(prev / "dense/codes.u8", dtype=np.uint8).reshape(-1, CODE_BYTES)
        assert len(h) == len(self.codes), f"{prev}: {len(h)} hashes for {len(self.codes)} codes"
        self.order = np.argsort(h)
        self.sorted = h[self.order]
        self.centroids = np.fromfile(prev / "dense/centroids.f32", dtype="<f4").reshape(-1, DIMS)

    def lookup(self, h: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(found mask, codes for the found rows)."""
        i = np.clip(np.searchsorted(self.sorted, h), 0, len(self.sorted) - 1)
        found = self.sorted[i] == h
        return found, self.codes[self.order[i[found]]]


def n_lists(n: int) -> int:
    # ~1,000–2,000 codes per list, like enwiki (41.5M codes, 16,384 lists).
    return int(2 ** np.clip(np.round(np.log2(max(n, 1) / 1500)), 4, 14))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--shard", type=int, default=50_000)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--batch-tokens", type=int, default=32_768)
    ap.add_argument("--reuse", help="previous work dir with dense/{codes.u8,hashes.u64,centroids.f32}")
    a = ap.parse_args()

    work = Path(a.work)
    shards = work / "dense" / "shards"
    shards.mkdir(parents=True, exist_ok=True)
    titles = pq.read_table(work / "articles.parquet", columns=["title"])["title"].to_pylist()
    pf = pq.ParquetFile(work / "passages.parquet")
    total = pf.metadata.num_rows

    enc = None
    reuse = Reuse(Path(a.reuse)) if a.reuse else None
    t0, done, reused = time.time(), 0, 0
    for s, batch in enumerate(pf.iter_batches(batch_size=a.shard, columns=["passage_id", "article_id", "section_path", "text"])):
        path = shards / f"{s:05d}.npy"
        if path.exists():
            continue
        pids = batch.column("passage_id").to_numpy()
        assert pids[0] == s * a.shard and pids[-1] == pids[0] + len(pids) - 1, "passage ids must be dense and sorted"
        texts = [doc_text(titles[aid], sec, t) for aid, sec, t in zip(
            batch.column("article_id").to_pylist(), batch.column("section_path").to_pylist(), batch.column("text").to_pylist())]
        codes = np.empty((len(texts), CODE_BYTES), dtype=np.uint8)
        found = np.zeros(len(texts), dtype=bool)
        if reuse:
            found, codes[found] = reuse.lookup(text_hashes(texts))
            reused += int(found.sum())
        if not found.all():
            enc = enc or Encoder(a.model, a.max_len, a.batch_tokens)
            codes[~found] = enc.codes([t for t, f in zip(texts, found) if not f])
        np.save(path.with_suffix(".tmp.npy"), codes)
        path.with_suffix(".tmp.npy").rename(path)
        done += len(codes)
        rate = done / (time.time() - t0)
        print(f"shard {s}: {pids[0] + len(codes)}/{total} passages, {rate:.0f}/s, eta {(total - pids[0] - len(codes)) / rate / 3600:.1f} h"
              + (f", {reused} reused" if reuse else ""), flush=True)

    codes = np.concatenate([np.load(shards / f"{s:05d}.npy") for s in range(-(-total // a.shard))])
    assert len(codes) == total, f"{len(codes)} codes for {total} passages"
    d = work / "dense"
    codes.tofile(d / "codes.u8")
    np.concatenate([text_hashes([doc_text(titles[aid], sec, t) for aid, sec, t in zip(
        b.column("article_id").to_pylist(), b.column("section_path").to_pylist(), b.column("text").to_pylist())])
        for b in pf.iter_batches(batch_size=a.shard, columns=["article_id", "section_path", "text"])]).tofile(d / "hashes.u64")
    if reuse:  # monthly: new codes join last month's lists (no rebalancing)
        centroids = reuse.centroids
        lists = len(centroids)
    else:
        lists = n_lists(len(codes))
        centroids = train_ivf(codes, lists, sample=min(len(codes), 2_000_000), niter=12)
    centroids.astype("<f4").tofile(d / "centroids.f32")
    assign_all(codes, centroids).astype("<u4").tofile(d / "assign.u32")
    (d / "info.json").write_text(json.dumps({"dims": DIMS, "n_lists": lists, "source": f"{a.model} fp16, first {DIMS} dims, sign"}))
    print(f"done: {len(codes)} codes, {lists} lists ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
