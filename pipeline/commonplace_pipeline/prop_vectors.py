"""Property vectors: the core picks the Wikidata facts a question asks about by the cosine between the
question's embedding and these vectors, with no word lists.

One vector per property of configs/wikidata-props.tsv, and one per ratio of two properties that the core
computes (population density). A vector is the mean query-encoder embedding of the label shown on the phone,
the Wikidata label and the English aliases (data/raw/wikidata, see scripts/fetch-wikidata.sh). Each text is
embedded by `commonplace encode` with the desktop (fp32) and the phone (int8) encoder, so one vector fits
both. A property with numeric values in the facts (data/work/wikidata, see wikidata.py) is a quantity.
Writes core/commonplace-core/src/wikidata_props.json (int8 as hex, with a scale per vector); the core
compiles it in. Run it again after a change to the property list or the query encoder.

  uv run --project pipeline python -m commonplace_pipeline.prop_vectors
"""

from __future__ import annotations

import argparse
import json

import duckdb
import numpy as np

from .heads import MODELS, ROOT, embed
from .wikidata import load_props

# id "numerator/denominator", the label of the ratio, and the Wikidata item whose names describe it.
DERIVED = [("P1082/P2046", "population density", "Q22856")]
OUT = ROOT / "core/commonplace-core/src/wikidata_props.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw/wikidata", help="output of scripts/fetch-wikidata.sh")
    ap.add_argument("--work", default="data/work/wikidata", help="output of commonplace_pipeline.wikidata")
    args = ap.parse_args()
    raw = ROOT / args.raw
    items = [(pid, label, pid) for pid, label in load_props()] + DERIVED
    names: dict[str, list[str]] = {item: [] for _, _, item in items}
    con = duckdb.connect()
    for table in ["wikidata-labels", "wikidata-aliases"]:
        files = [str(raw / table / lang / "*.parquet") for lang in ["en", "mul"]]
        rows = con.execute("SELECT id, value FROM read_parquet(?) WHERE id IN (SELECT unnest(?)) ORDER BY id, value",
                           [files, list(names)]).fetchall()
        for item, value in rows:
            names[item].append(" ".join(value.split()))
    quantities = {pid for (pid,) in con.execute("SELECT DISTINCT pid FROM read_parquet(?) WHERE value_num IS NOT NULL",
                                                [str(ROOT / args.work / "facts.parquet")]).fetchall()}
    texts: list[str] = []
    owner: list[int] = []
    for i, (_, label, item) in enumerate(items):
        seen: set[str] = set()
        for t in [label, *names[item]]:
            if t and t.lower() not in seen:
                seen.add(t.lower())
                texts.append(t)
                owner.append(i)
    emb = sum(embed(texts, m) for m in MODELS)
    owner_arr = np.array(owner)
    out = []
    for i, (key, label, _) in enumerate(items):
        v = emb[owner_arr == i].mean(axis=0)
        v /= np.linalg.norm(v)
        scale = float(np.abs(v).max()) / 127
        q = np.round(v / scale).astype(np.int8)
        quantity = all(p in quantities for p in key.split("/"))
        out.append({"id": key, "label": label, "quantity": quantity, "scale": round(scale, 8), "v": q.tobytes().hex()})
    OUT.write_text(json.dumps({"dims": int(emb.shape[1]), "props": out}) + "\n")
    print(f"{len(out)} vectors from {len(texts)} texts -> {OUT}")


if __name__ == "__main__":
    main()
