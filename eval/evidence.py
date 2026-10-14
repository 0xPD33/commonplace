#!/usr/bin/env python3
"""Label the evidence of a run with a Claude judge: could the question be answered from it?

Reads the `evidence` that run.py stored with each answer (the text the model saw) and writes
eval/runs/<run-id>/evidence.jsonl: `sufficiency` (full, partial or none), the numbers of the
relevant passages and what is missing. A low judge score with full evidence is a generator
failure; with partial or no evidence it is a retrieval failure. Skips ids already labeled.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from common import RUNS, append_jsonl, claude_json, claude_meta, claude_version, read_jsonl

PROMPT_VERSION = 1
SYSTEM = f"""You check whether retrieved evidence is enough to answer a question. Today is {date.today().isoformat()}.
You see a question, numbered passages from an offline library and sometimes Wikidata facts.
Decide from this evidence alone, not from what you know.
- sufficiency: "full" when every part of the question can be answered from the evidence;
  "partial" when part of the answer is missing or only hinted at; "none" when the evidence
  does not address the question.
- relevant: the numbers of the passages that carry facts the answer needs.
- missing: what the evidence lacks, under 20 words; empty when sufficiency is "full"."""

SCHEMA = {
    "type": "object",
    "properties": {"sufficiency": {"type": "string", "enum": ["full", "partial", "none"]},
                   "relevant": {"type": "array", "items": {"type": "integer"}},
                   "missing": {"type": "string"}},
    "required": ["sufficiency", "relevant", "missing"],
}


def prompt_for(row):
    turns = "".join(f"User: {h['query']}\nAssistant: {h['answer']}\n" for h in row.get("history", [])[-2:])
    context = f"Earlier conversation:\n{turns}\nThe question continues this conversation.\n\n" if turns else ""
    ev = row["evidence"]
    passages = "\n\n".join(
        f"[{s['n']}] {s['title']}" + (f" — {s['section']}" if s["section"] else "") + f" ({s['pack_id']}):\n{s['text']}"
        for s in ev["sources"])
    return f"{context}Question: {row['query']}\n\n=== Evidence ===\n{passages}" + (f"\n\n{ev['extra']}" if ev["extra"] else "")


def label(row, model, budget, version):
    out, raw = claude_json(prompt_for(row), SYSTEM, SCHEMA, model, "", budget)
    return {"id": row["id"], "category": row.get("category"), **out, "n_sources": len(row["evidence"]["sources"]),
            "requested_model": model, "cli_version": version, "prompt_version": PROMPT_VERSION, **claude_meta(raw)}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id under eval/runs/")
    ap.add_argument("--subset", help="comma list of query ids and/or categories")
    ap.add_argument("--model", default="sonnet", help="claude --model value")
    ap.add_argument("--budget-usd", type=float, default=0.3, help="cap per call")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    out = RUNS / a.run / "evidence.jsonl"
    done = {j["id"] for j in read_jsonl(out)}
    keys = set(a.subset.split(",")) if a.subset else None
    rows = read_jsonl(RUNS / a.run / "answers.jsonl")
    todo = [r for r in rows if r["id"] not in done and (r.get("evidence") or {}).get("sources")
            and (not keys or r["id"] in keys or r.get("category") in keys)]
    print(f"{len(todo)} to label ({sum(1 for r in rows if 'evidence' not in r)} answers have no evidence)", file=sys.stderr)

    version = claude_version()
    failed = 0
    with ThreadPoolExecutor(a.jobs) as pool:
        futures = {pool.submit(label, r, a.model, a.budget_usd, version): r["id"] for r in todo}
        for f, qid in futures.items():
            try:
                j = f.result()
                append_jsonl(out, j)
                print(f"{qid}: {j['sufficiency']} relevant={j['relevant']}", file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"{qid}: FAILED {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
