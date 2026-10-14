#!/usr/bin/env python3
"""Retrieval recall@10 and @40 against gold articles, no LLM.

Runs `commonplace retrieve --json` for every query of a run, with the run's binary and retrieval
flags, and writes eval/runs/<run-id>/recall.jsonl. The @10 list comes from `--k 10`, so the
rerank pool is the run's own `--fuse-keep`; the @40 list comes from `--k 40`. Per query:
- recall@k: gold from any pack (see gold.py), the headline;
- recall_wiki@k: gold from Wikipedia titles and URLs only (the earlier method);
- recall@ev: gold among the passages the model saw in the run's answer (Wikidata sources do not count).
A follow-up thread is searched with the text that the answer actually searched (`search_query`).
Chat and reformat turns do not search: they get no recall and count no gold.
"""

import argparse
import json
import subprocess
import sys

from common import BIN, REPO, RUNS, cli_flags, load_queries, norm_title, read_jsonl
from gold import Resolver, gold_for

KS = (10, 40)


def recall(gold, titles):
    return len(gold & titles) / len(gold) if gold else None


def retrieve(meta, flags, query, k):
    r = subprocess.run([meta.get("bin") or str(BIN), "--library", meta["library"], *flags, "retrieve", "--json",
                        "--k", str(k), query], cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"retrieve failed for {query!r}: {r.stderr[-500:]}")
    return json.loads(r.stdout)["hits"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id under eval/runs/")
    ap.add_argument("--library", help="search another library than the run's (R@ev still counts the run's sources)")
    ap.add_argument("--out", default="recall.jsonl", help="file name in the run directory")
    a = ap.parse_args()

    out = RUNS / a.run
    meta = json.loads((out / "meta.json").read_text())
    ids = set(meta["query_ids"])
    queries = [q for q in load_queries(REPO / meta["queries"]) if q["id"] in ids]
    answers = {r["id"]: r for r in read_jsonl(out / "answers.jsonl")}
    flags = cli_flags(f for f in meta["flags"] if f != "no-llm") + meta.get("cli", [])
    meta["library"] = a.library or meta["library"]
    resolver = Resolver(meta["library"])

    with open(out / a.out, "w") as f:
        for q in queries:
            g, g_wiki = gold_for(q, resolver), gold_for(q, resolver, wikipedia_only=True)
            ans = answers.get(q["id"], {})
            search = (ans.get("record") or {}).get("search_query") if q.get("history") else None
            searched = ans.get("kind", "Question") == "Question"
            hits = {k: retrieve(meta, flags, search or q["query"], k) if searched else [] for k in KS}
            # Match on canonical title: the same article can live in several packs
            # (enwiki and enwiki-core) under different article ids.
            gold = {norm_title(x["matched_title"]) for x in g["gold"]}
            gold_wiki = {norm_title(x["matched_title"]) for x in g_wiki["gold"]}
            seen = [norm_title(s["title"]) for s in ans.get("sources", []) if not s["pack_id"].startswith("wikidata")]
            row = {"id": q["id"], "category": q.get("category"), "n_gold": len(gold) * searched,
                   "n_gold_wiki": len(gold_wiki) * searched,
                   "gold": sorted({f"{x['pack_id']}: {x['matched_title']}" for x in g["gold"]}),
                   "unresolved": g["unresolved"]}
            for k in KS:
                titles = {norm_title(h["title"]) for h in hits[k]}
                row[f"recall@{k}"] = recall(gold, titles) if searched else None
                row[f"recall_wiki@{k}"] = recall(gold_wiki, titles) if searched else None
            row["recall@ev"] = recall(gold, set(seen)) if seen else None
            row["top"] = list(dict.fromkeys(f"{h['pack_id']}: {h['title']}" for h in hits[10]))
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{q['id']}: gold={len(gold)} r@10={row['recall@10']} r@40={row['recall@40']} "
                  f"old r@10={row['recall_wiki@10']} ev={row['recall@ev']}", file=sys.stderr)
    print(out / a.out)


if __name__ == "__main__":
    main()
