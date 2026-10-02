#!/usr/bin/env python3
"""Retrieval recall@10 and @40 against gold articles, no LLM.

Runs `commonplace retrieve --json --k 40` for every query of a run, with the run's
retrieval flags, and writes eval/runs/<run-id>/recall.jsonl.
"""

import argparse
import json
import subprocess
import sys

from common import BIN, REPO, RUNS, cli_flags, load_queries, norm_title
from gold import Resolver, gold_for

KS = (10, 40)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id under eval/runs/")
    a = ap.parse_args()

    out = RUNS / a.run
    meta = json.loads((out / "meta.json").read_text())
    ids = set(meta["query_ids"])
    queries = [q for q in load_queries(REPO / meta["queries"]) if q["id"] in ids]
    flags = cli_flags(f for f in meta["flags"] if f != "no-llm") + meta.get("cli", [])
    resolver = Resolver(meta["library"])

    with open(out / "recall.jsonl", "w") as f:
        for q in queries:
            g = gold_for(q, resolver)
            r = subprocess.run([str(BIN), "--library", meta["library"], *flags, "retrieve", "--json", "--k", str(max(KS)),
                                q["query"]], cwd=REPO, capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"{q['id']}: retrieve failed: {r.stderr[-500:]}")
            hits = json.loads(r.stdout)["hits"]
            # Match on canonical title: the same article can live in several packs
            # (enwiki and enwiki-core) under different article ids.
            gold = {norm_title(x["matched_title"]) for x in g["gold"]}
            row = {"id": q["id"], "category": q.get("category"), "n_gold": len(gold),
                   "gold": sorted({x["matched_title"] for x in g["gold"]}), "unresolved": g["unresolved"]}
            for k in KS:
                found = gold & {norm_title(h["title"]) for h in hits[:k]}
                row[f"recall@{k}"] = len(found) / len(gold) if gold else None
            row["top_titles"] = list(dict.fromkeys(h["title"] for h in hits[:10]))
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{q['id']}: gold={len(gold)} r@10={row['recall@10']} r@40={row['recall@40']}",
                  file=sys.stderr)
    print(out / "recall.jsonl")


if __name__ == "__main__":
    main()
