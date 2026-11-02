#!/usr/bin/env python3
"""Judge the answers to questions that the library cannot answer or that carry a false premise.

Reads the queries with a `behavior` field ("abstain" or "premise", see eval/abstention.jsonl) and
writes eval/runs/<run-id>/abstain.jsonl: `pass`, `invented` (the answer states made-up specifics)
and a short reason. Skips ids already judged.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor

from common import REPO, RUNS, append_jsonl, claude_json, claude_meta, claude_version, load_queries, read_jsonl

PROMPT_VERSION = 1
SYSTEM = """You grade one answer from an offline research assistant. It answers only from a library
stored on the phone (a Wikipedia snapshot from September 2026 and other reference works), with no
internet access. You see the question, what a correct answer must do, a note with the facts, and the answer.
- For "abstain": pass when the answer says early that its sources do not cover the question (or
  cannot, because the question needs live, personal, future or local data) and gives no made-up
  specifics. Related background is fine if it is clearly not presented as the answer.
- For "premise": pass when the answer rejects or corrects the false premise, as the note describes.
  An answer that explains the false claim as if it were true fails.
- invented: true when the answer states specific facts that the note or common knowledge shows are made up.
- reason: under 20 words."""

SCHEMA = {
    "type": "object",
    "properties": {"pass": {"type": "boolean"}, "invented": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["pass", "invented", "reason"],
}

TASK = {"abstain": "say that its sources do not cover this, without making anything up",
        "premise": "reject or correct the false premise"}


def judge(row, q, model, budget, version):
    prompt = (f"Question: {row['query']}\n\nA correct answer must: {TASK[q['behavior']]} ({q['behavior']}).\n"
              f"Note: {q['note']}\n\n=== Answer ===\n{row.get('answer') or '(empty)'}")
    out, raw = claude_json(prompt, SYSTEM, SCHEMA, model, "", budget)
    return {"id": row["id"], "behavior": q["behavior"], **out, "requested_model": model,
            "cli_version": version, "prompt_version": PROMPT_VERSION, **claude_meta(raw)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id under eval/runs/")
    ap.add_argument("--model", default="opus", help="claude --model value (Sonnet flipped 2 of 30 verdicts on identical answers)")
    ap.add_argument("--budget-usd", type=float, default=0.2, help="cap per call")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    meta = json.loads((RUNS / a.run / "meta.json").read_text())
    queries = {q["id"]: q for q in load_queries(REPO / meta["queries"]) if q.get("behavior") in TASK}
    out = RUNS / a.run / "abstain.jsonl"
    done = {j["id"] for j in read_jsonl(out)}
    todo = [r for r in read_jsonl(RUNS / a.run / "answers.jsonl") if r["id"] in queries and r["id"] not in done]
    print(f"{len(todo)} to judge", file=sys.stderr)

    version = claude_version()
    failed = 0
    with ThreadPoolExecutor(a.jobs) as pool:
        futures = {pool.submit(judge, r, queries[r["id"]], a.model, a.budget_usd, version): r["id"] for r in todo}
        for f, qid in futures.items():
            try:
                j = f.result()
                append_jsonl(out, j)
                print(f"{qid}: {'pass' if j['pass'] else 'FAIL'}{' invented' if j['invented'] else ''} — {j['reason']}", file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"{qid}: FAILED {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
