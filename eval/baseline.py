#!/usr/bin/env python3
"""Frontier baseline: Claude with web search answers each query once.

Writes eval/baselines/<id>.json and skips ids that already have a file.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from common import (BASELINES, REPO, claude_json, claude_meta, claude_version, load_queries, now,
                    write_json)

PROMPT_VERSION = 1
SYSTEM = f"""You are a careful research assistant. Today is {date.today().isoformat()}.
Use web search to check facts before you answer. Answer the user's question the way an
expert would on a phone: plain prose, usually 2 to 6 sentences, longer only when the question
needs it. Cite sources inline as [1], [2] and list each cited source with its URL and title.
If the question cannot be answered reliably, say so instead of guessing.
Also list the English Wikipedia article titles that cover the facts in your answer, whether
or not you cited Wikipedia."""

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"n": {"type": "integer"}, "url": {"type": "string"},
                               "title": {"type": "string"}},
                "required": ["n", "url", "title"],
            },
        },
        "wikipedia_articles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "sources", "wikipedia_articles"],
}


def prompt_for(q):
    turns = "".join(f"Earlier question: {h['query']}\nEarlier answer: {h['answer']}\n\n"
                    for h in q.get("history", []))
    return f"{turns}Question: {q['query']}"


def baseline(q, model, budget, version):
    path = BASELINES / f"{q['id']}.json"
    out, raw = claude_json(prompt_for(q), SYSTEM, SCHEMA, model, "WebSearch,WebFetch", budget)
    write_json(path, {
        "id": q["id"],
        "query": q["query"],
        **out,
        "urls": [s["url"] for s in out["sources"]],
        "requested_model": model,
        **claude_meta(raw),
        "cli_version": version,
        "prompt_version": PROMPT_VERSION,
        "created": now(),
    })
    return q["id"], raw.get("total_cost_usd")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", default="eval/dev.jsonl")
    ap.add_argument("--subset", help="comma list of query ids and/or categories")
    ap.add_argument("--model", default="opus", help="claude --model value")
    ap.add_argument("--budget-usd", type=float, default=1.0, help="cap per query")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    BASELINES.mkdir(exist_ok=True)
    todo = [q for q in load_queries(REPO / a.queries, a.subset)
            if not (BASELINES / f"{q['id']}.json").exists()]
    print(f"{len(todo)} baselines to fetch", file=sys.stderr)
    version = claude_version()
    failed = 0
    with ThreadPoolExecutor(a.jobs) as pool:
        futures = {pool.submit(baseline, q, a.model, a.budget_usd, version): q["id"] for q in todo}
        for f, qid in futures.items():
            try:
                _, cost = f.result()
                print(f"{qid}: ok (${cost:.3f})", file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"{qid}: FAILED {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
