"""Plan A (PLAN.md §6.4): turn the prebuilt mxbai binary codes and the row-aligned 2023-11
Wikipedia passages into packbuild input, with no embedding compute.

Subcommands:
  verify   re-embed a few rows with mxbai (CPU) and check they match the prebuilt codes
  convert  write articles/passages parquet + dense/{codes.u8,assign.u32,centroids.f32,info.json}
  subset   derive a smaller pack (e.g. enwiki-core) from a converted directory
  enrich   mark disambiguation pages and add redirects from Wikidata labels and aliases
  tail     write the passages the prebuilt codes miss as a work directory for embed.py
  extend   append the tail's embed.py codes to the prebuilt prefix
"""

from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

DIMS = 512
CODE_BYTES = DIMS // 8
# The prebuilt index matches the text row for row only through text shard 34 (verified by re-embedding
# with mxbai; see docs/DATASETS.md). Later codes match no text row, so dense covers this prefix only.
ALIGNED_ROWS = 35_416_680


def load_codes(index_path: str) -> np.ndarray:
    import faiss

    idx = faiss.read_index_binary(index_path)
    raw = faiss.vector_to_array(idx.xb) if hasattr(idx, "xb") else None
    if raw is None:
        raise SystemExit(f"{index_path}: unsupported binary index type {type(idx)}")
    codes = raw.reshape(idx.ntotal, idx.code_size)
    print(f"codes: {codes.shape} ({idx.d} bits)")
    return codes


def text_files(d: str) -> list[str]:
    files = sorted(glob.glob(f"{d}/train-*.parquet"))
    if not files:
        raise SystemExit(f"no parquet files in {d}")
    return files


def pv_key(title: str) -> str:
    """Pageview titles are lowercase with underscores."""
    return title.lower().replace(" ", "_")


def first_sentence(t: str) -> str:
    for sep in (". ", "! ", "? "):
        i = t.find(sep)
        if 0 < i < 300:
            return t[: i + 1]
    return t[:300]


def cmd_verify(a) -> None:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    codes = load_codes(a.index)
    tok = Tokenizer.from_file(f"{a.mxbai}/tokenizer.json")
    tok.enable_truncation(512)
    tok.enable_padding()
    sess = ort.InferenceSession(f"{a.mxbai}/onnx/model.onnx")
    rng = np.random.default_rng(0)
    pf = pq.ParquetFile(text_files(a.text)[0])
    table = pf.read_row_group(0, columns=["title", "text"])
    rows = sorted(rng.choice(table.num_rows, size=a.n, replace=False).tolist())
    titles = [table["title"][i].as_py() for i in rows]
    texts = [table["text"][i].as_py() for i in rows]
    variants = {"text": texts, "title\\ntext": [f"{t}\n{x}" for t, x in zip(titles, texts)], "title text": [f"{t} {x}" for t, x in zip(titles, texts)]}
    for name, docs in variants.items():
        enc = tok.encode_batch(docs)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        out = sess.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)})[0]
        emb = out[:, 0, :]  # CLS pooling
        bits = np.packbits(emb > 0, axis=1)
        ham = [int(np.unpackbits(bits[k] ^ codes[r]).sum()) for k, r in enumerate(rows)]
        print(f"{name:12s} hamming over {codes.shape[1] * 8} bits: median {np.median(ham):.0f}, max {max(ham)} (random ≈ {codes.shape[1] * 4})")


def cmd_convert(a) -> None:
    out = Path(a.out)
    (out / "dense").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    codes = load_codes(a.index)[:ALIGNED_ROWS, :CODE_BYTES]

    # Pageviews by title.
    views: dict[str, int] = {}
    if a.pageviews:
        con = sqlite3.connect(f"file:{a.pageviews}?immutable=1", uri=True)
        for title, v in con.execute("SELECT title, views FROM pages"):
            views[title] = v
        print(f"pageviews: {len(views)} titles ({time.time() - t0:.0f}s)")
    qids: dict[str, str] = {}
    if a.title_qid and Path(a.title_qid).exists():
        t = pq.read_table(a.title_qid)
        qids = dict(zip(t["title"].to_pylist(), t["qid"].to_pylist()))
        print(f"qids: {len(qids)}")

    arts = {k: [] for k in ["article_id", "title", "qid", "popularity", "url_title", "oneliner", "first_passage", "n_passages"]}
    seen_pages: set[str] = set()
    split_articles = 0
    pw = None
    pid = 0
    cur_page = None
    for f in text_files(a.text):
        for batch in pq.ParquetFile(f).iter_batches(batch_size=65536, columns=["_id", "title", "text"]):
            ids = batch.column("_id").to_pylist()
            titles = batch.column("title").to_pylist()
            texts = batch.column("text").to_pylist()
            aid_col, ord_col = [], []
            for _id, title, text in zip(ids, titles, texts):
                page = _id.rsplit("_", 1)[0]
                if page != cur_page:
                    if page in seen_pages:
                        split_articles += 1
                    seen_pages.add(page)
                    cur_page = page
                    arts["article_id"].append(len(arts["title"]))
                    arts["title"].append(title)
                    arts["qid"].append(qids.get(title))
                    arts["popularity"].append(views.get(pv_key(title), 0))
                    arts["url_title"].append(title.replace(" ", "_"))
                    arts["oneliner"].append(first_sentence(text))
                    arts["first_passage"].append(pid)
                    arts["n_passages"].append(0)
                arts["n_passages"][-1] += 1
                aid_col.append(len(arts["title"]) - 1)
                ord_col.append(min(arts["n_passages"][-1] - 1, 65535))
                pid += 1
            t = pa.table({
                "passage_id": pa.array(range(pid - len(ids), pid), pa.uint32()),
                "article_id": pa.array(aid_col, pa.uint32()),
                "ordinal": pa.array(ord_col, pa.uint16()),
                "section_path": pa.array([""] * len(ids), pa.string()),
                "text": pa.array(texts, pa.string()),
            })
            if pw is None:
                pw = pq.ParquetWriter(out / "passages.parquet", t.schema, compression="zstd")
            pw.write_table(t)
        print(f"{Path(f).name}: {pid} passages, {len(arts['title'])} articles ({time.time() - t0:.0f}s)")
    pw.close()
    if pid < len(codes):
        raise SystemExit(f"only {pid} text rows for {len(codes)} codes")
    print(f"articles split across non-adjacent rows: {split_articles}")
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

    codes.tofile(out / "dense/codes.u8")
    centroids = train_ivf(codes, a.lists, a.sample, a.niter)
    centroids.astype("<f4").tofile(out / "dense/centroids.f32")
    assign_all(codes, centroids).astype("<u4").tofile(out / "dense/assign.u32")
    (out / "dense/info.json").write_text(json.dumps({"dims": DIMS, "n_lists": a.lists, "source": "sentence-transformers/quantized-retrieval-data (first 512 bits)"}))
    print(f"done in {time.time() - t0:.0f}s")


def signs(codes: np.ndarray) -> np.ndarray:
    return np.unpackbits(codes, axis=1).astype(np.float32) * 2 - 1


def train_ivf(codes: np.ndarray, lists: int, sample: int, niter: int) -> np.ndarray:
    import faiss

    rng = np.random.default_rng(0)
    idx = np.sort(rng.choice(codes.shape[0], size=min(sample, codes.shape[0]), replace=False))
    x = signs(codes[idx]) / np.sqrt(DIMS)
    t0 = time.time()
    km = faiss.Kmeans(DIMS, lists, niter=niter, spherical=True, seed=0, verbose=True, max_points_per_centroid=max(1, sample // lists + 1))
    km.train(x)
    print(f"k-means {lists} lists on {len(x)} codes: {time.time() - t0:.0f}s")
    return km.centroids


def assign_all(codes: np.ndarray, centroids: np.ndarray, batch: int = 500_000) -> np.ndarray:
    import faiss

    index = faiss.IndexFlatIP(DIMS)
    index.add(centroids)
    out = np.empty(codes.shape[0], dtype=np.uint32)
    t0 = time.time()
    for s in range(0, codes.shape[0], batch):
        _, I = index.search(signs(codes[s : s + batch]), 1)
        out[s : s + batch] = I[:, 0]
        if (s // batch) % 10 == 0:
            print(f"assign {s + batch}/{codes.shape[0]} ({time.time() - t0:.0f}s)")
    counts = np.bincount(out, minlength=len(centroids))
    print(f"list sizes: min {counts.min()} median {int(np.median(counts))} max {counts.max()}")
    return out


def cmd_subset(a) -> None:
    """Leads (ordinal 0) of the top --leads articles plus all passages of the top --full articles."""
    src, out = Path(a.src), Path(a.out)
    dense = (src / "dense").exists()  # keyword-only packs (enwiki-extra) have no dense index
    (out / "dense" if dense else out).mkdir(parents=True, exist_ok=True)
    arts = pq.read_table(src / "articles.parquet").to_pydict()
    order = np.argsort(-np.array(arts["popularity"], dtype=np.int64), kind="stable")
    full = set(order[: a.full].tolist())
    lead = set(order[: a.leads].tolist())
    keep_articles = sorted(full | lead)
    new_aid = {old: i for i, old in enumerate(keep_articles)}
    # Passage selection in original order, contiguous per article.
    sel: list[int] = []
    new_arts = {k: [] for k in arts}
    for old in keep_articles:
        fp, np_ = arts["first_passage"][old], arts["n_passages"][old]
        take = range(fp, fp + np_) if old in full else range(fp, fp + 1)
        for k in arts:
            new_arts[k].append(arts[k][old])
        new_arts["article_id"][-1] = new_aid[old]
        new_arts["first_passage"][-1] = len(sel)
        new_arts["n_passages"][-1] = len(take)
        sel.extend(take)
    sel_arr = np.array(sel, dtype=np.int64)
    print(f"subset: {len(keep_articles)} articles, {len(sel)} passages")
    schema = pq.read_schema(src / "articles.parquet")
    pq.write_table(pa.table(new_arts, schema=schema), out / "articles.parquet", compression="zstd")
    if (src / "redirects.parquet").exists():
        red = pq.read_table(src / "redirects.parquet").to_pydict()
        keep = [(f, new_aid[x]) for f, x in zip(red["from_title"], red["article_id"]) if x in new_aid]
        pq.write_table(
            pa.table({"from_title": pa.array([k[0] for k in keep], pa.string()), "article_id": pa.array([k[1] for k in keep], pa.uint32())}),
            out / "redirects.parquet",
            compression="zstd",
        )

    # Stream passages, keeping selected rows (sel is sorted because articles keep their original order).
    sel_set = np.zeros(int(sel_arr.max()) + 1, dtype=bool)
    sel_set[sel_arr] = True
    pw = None
    new_pid = 0
    for batch in pq.ParquetFile(src / "passages.parquet").iter_batches(batch_size=262144):
        pids = batch.column("passage_id").to_numpy()
        mask = pids < len(sel_set)
        mask[mask] = sel_set[pids[mask]]
        if not mask.any():
            continue
        t = pa.Table.from_batches([batch]).filter(pa.array(mask))
        m = t.num_rows
        aids = [new_aid[x] for x in t.column("article_id").to_pylist()]
        t = t.set_column(0, "passage_id", pa.array(range(new_pid, new_pid + m), pa.uint32()))
        t = t.set_column(1, "article_id", pa.array(aids, pa.uint32()))
        new_pid += m
        if pw is None:
            pw = pq.ParquetWriter(out / "passages.parquet", t.schema, compression="zstd")
        pw.write_table(t)
    pw.close()
    if not dense:
        print(f"wrote {new_pid} passages (no dense index)")
        return
    codes = np.fromfile(src / "dense/codes.u8", dtype=np.uint8).reshape(-1, CODE_BYTES)
    assign = np.fromfile(src / "dense/assign.u32", dtype="<u4")
    covered = sel_arr < len(assign)  # dense covers only a prefix of the source passages
    codes[sel_arr[covered]].tofile(out / "dense/codes.u8")
    assign[sel_arr[covered]].tofile(out / "dense/assign.u32")
    np.nonzero(covered)[0].astype("<u4").tofile(out / "dense/ids.u32")
    print(f"dense covers {int(covered.sum())} of {len(sel_arr)} passages")
    for f in ["centroids.f32", "info.json"]:
        (out / "dense" / f).write_bytes((src / "dense" / f).read_bytes())
    print(f"wrote {new_pid} passages")


def cmd_tail(a) -> None:
    """Write the passages that the prebuilt codes miss (rows from ALIGNED_ROWS on), renumbered from 0,
    as a work directory for embed.py. The articles table is shared, so article ids stay valid."""
    src, out = Path(a.src), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "articles.parquet").unlink(missing_ok=True)
    (out / "articles.parquet").symlink_to((src / "articles.parquet").resolve())
    pw, n = None, 0
    for batch in pq.ParquetFile(src / "passages.parquet").iter_batches(batch_size=262144):
        pids = batch.column("passage_id").to_numpy()
        if pids[-1] < ALIGNED_ROWS:
            continue
        t = pa.Table.from_batches([batch]).filter(pa.array(pids >= ALIGNED_ROWS))
        t = t.set_column(0, "passage_id", pa.array(range(n, n + t.num_rows), pa.uint32()))
        n += t.num_rows
        pw = pw or pq.ParquetWriter(out / "passages.parquet", t.schema, compression="zstd")
        pw.write_table(t)
    pw.close()
    print(f"tail: {n} passages from row {ALIGNED_ROWS}")


def cmd_extend(a) -> None:
    """Append the tail's embed.py codes to the prebuilt prefix; the tail joins the existing IVF lists."""
    d, tail = Path(a.dir) / "dense", Path(a.tail) / "dense"
    codes = np.fromfile(d / "codes.u8", dtype=np.uint8).reshape(-1, CODE_BYTES)
    assert len(codes) == ALIGNED_ROWS, f"{d}/codes.u8 has {len(codes)} codes, expected the Plan A prefix {ALIGNED_ROWS}"
    extra = np.fromfile(tail / "codes.u8", dtype=np.uint8).reshape(-1, CODE_BYTES)
    centroids = np.fromfile(d / "centroids.f32", dtype="<f4").reshape(-1, DIMS)
    assign = np.concatenate([np.fromfile(d / "assign.u32", dtype="<u4"), assign_all(extra, centroids)])
    np.concatenate([codes, extra]).tofile(d / "codes.u8")
    assign.astype("<u4").tofile(d / "assign.u32")
    info = json.loads((d / "info.json").read_text())
    info["source"] += f"; rows {ALIGNED_ROWS}+ embedded by embed.py"
    (d / "info.json").write_text(json.dumps(info))
    print(f"dense now covers {len(assign)} rows")


def _norm(s: str) -> str:
    return " ".join(s.replace("_", " ").split()).lower()


def safe_alias(alias: str) -> bool:
    """Multi-word aliases, capitalized single words of 4+ letters ("Beantown"), and all-caps codes of
    2-5 letters ("US", "UK", "NATO"; the most-viewed claimant wins). Lowercase single words ("space",
    "everything") would hijack ordinary queries; the linker only takes single words written capitalized."""
    words = alias.split()
    if len(words) >= 2:
        return len(alias) <= 80
    if alias.isascii() and alias.isalpha() and alias.isupper():
        return 2 <= len(alias) <= 5
    return len(alias) >= 4 and alias[0].isupper()


def cmd_enrich(a) -> None:
    """Add `linkable` (False for disambiguation pages) to articles.parquet and write
    redirects.parquet from Wikidata labels and aliases."""
    d = Path(a.dir)
    arts = pq.read_table(d / "articles.parquet")
    n = arts.num_rows
    titles = arts["title"].to_pylist()
    if a.pageviews:
        con = sqlite3.connect(f"file:{a.pageviews}?immutable=1", uri=True)
        views = dict(con.execute("SELECT title, views FROM pages"))
        pop = [views.get(pv_key(t), 0) for t in titles]
        arts = arts.set_column(arts.column_names.index("popularity"), "popularity", pa.array(pop, pa.uint64()))
        print(f"pageviews: {sum(1 for x in pop if x)} of {n} articles matched")
    if a.title_qid:
        tq = pq.read_table(a.title_qid)
        by_title = dict(zip(tq["title"].to_pylist(), tq["qid"].to_pylist()))
        old = arts["qid"].to_pylist()
        new = [q or by_title.get(t) for q, t in zip(old, titles)]
        arts = arts.set_column(arts.column_names.index("qid"), "qid", pa.array(new, pa.string()))
        print(f"qids: {sum(1 for q in old if q)} → {sum(1 for q in new if q)} of {n} articles")
    linkable = np.array(["(disambiguation)" not in t for t in titles])
    for batch in pq.ParquetFile(d / "passages.parquet").iter_batches(batch_size=262144, columns=["article_id", "ordinal", "text"]):
        aid = batch.column("article_id").to_numpy()
        first = batch.column("ordinal").to_numpy() == 0
        texts = batch.column("text").to_pylist()
        for i in np.nonzero(first)[0]:
            head = texts[i][:400]
            if "may refer to" in head or "may also refer to" in head:
                linkable[aid[i]] = False
    print(f"disambiguation pages: {int((~linkable).sum())} of {n}")
    if "linkable" in arts.column_names:
        arts = arts.drop(["linkable"])
    pq.write_table(arts.append_column("linkable", pa.array(linkable)), d / "articles.parquet", compression="zstd")

    pop = arts["popularity"].to_numpy()
    qids = arts["qid"].to_pylist()
    by_qid: dict[str, int] = {}
    for i, q in enumerate(qids):
        if q and linkable[i] and (q not in by_qid or pop[i] > pop[by_qid[q]]):
            by_qid[q] = i
    taken = {_norm(t) for t, ok in zip(titles, linkable) if ok}
    best: dict[str, int] = {}
    ents = pq.read_table(a.entities, columns=["qid", "label", "aliases"])
    for q, label, aliases in zip(ents["qid"].to_pylist(), ents["label"].to_pylist(), ents["aliases"].to_pylist()):
        art = by_qid.get(q)
        if art is None:
            continue
        names = ([label] if label else []) + (aliases.split("|") if aliases else [])
        # A title like "Light Bulb (Abbott Elementary)" says Wikipedia finds the bare name ambiguous,
        # so the bare name must not redirect there ("light bulb" is not a sitcom episode).
        bare = _norm(titles[art].split(" (")[0]) if titles[art].endswith(")") else None
        for name in names:
            name = name.strip()
            if bare is not None and _norm(name) == bare:
                continue
            # All-caps codes keep their case as the key ("US" is not the film "Us"); the linker looks
            # them up exactly, so they never shadow a title.
            code = name.isascii() and name.isalpha() and name.isupper()
            key = name if code else _norm(name)
            if not key or (key in taken and not code) or not safe_alias(name):
                continue
            if key not in best or pop[art] > pop[best[key]]:
                best[key] = art
    pq.write_table(
        pa.table({"from_title": pa.array(list(best.keys()), pa.string()), "article_id": pa.array(list(best.values()), pa.uint32())}),
        d / "redirects.parquet",
        compression="zstd",
    )
    print(f"redirects: {len(best)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--index", required=True)
    v.add_argument("--text", required=True)
    v.add_argument("--mxbai", required=True)
    v.add_argument("--n", type=int, default=16)
    c = sub.add_parser("convert")
    c.add_argument("--index", required=True)
    c.add_argument("--text", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--pageviews")
    c.add_argument("--title-qid")
    c.add_argument("--lists", type=int, default=16384)
    c.add_argument("--sample", type=int, default=2_000_000)
    c.add_argument("--niter", type=int, default=12)
    s = sub.add_parser("subset")
    s.add_argument("--src", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--leads", type=int, default=2_000_000)
    s.add_argument("--full", type=int, default=200_000)
    e = sub.add_parser("enrich")
    e.add_argument("--dir", required=True)
    e.add_argument("--entities", default="data/work/wikidata/entities.parquet")
    e.add_argument("--pageviews", help="recompute popularity from pageviews.sqlite")
    e.add_argument("--title-qid", help="fill missing QIDs by exact title (data/work/wikidata/title_qid.parquet)")
    t = sub.add_parser("tail")
    t.add_argument("--src", required=True, help="converted Plan A directory")
    t.add_argument("--out", required=True)
    x = sub.add_parser("extend")
    x.add_argument("--dir", required=True, help="converted Plan A directory (dense/ holds the prefix codes)")
    x.add_argument("--tail", required=True, help="the tail directory after embed.py")
    a = ap.parse_args()
    {"verify": cmd_verify, "convert": cmd_convert, "subset": cmd_subset, "enrich": cmd_enrich, "tail": cmd_tail,
     "extend": cmd_extend}[a.cmd](a)


if __name__ == "__main__":
    main()
