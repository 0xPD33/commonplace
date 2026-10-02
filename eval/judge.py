#!/usr/bin/env python3
"""Blind pairwise grading of a run against the baseline with a Claude judge.

Each query is graded twice with the answers swapped between A and B; the scores are
averaged. Writes eval/runs/<run-id>/judgments.jsonl and skips ids already judged.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from common import (BASELINES, RUNS, append_jsonl, claude_json, claude_meta, claude_version,
                    read_jsonl)

PROMPT_VERSION = 2
CRITERIA = {"correctness": 4, "completeness": 3, "groundedness": 2, "usefulness": 1}
SYSTEM = f"""You grade two answers to the same question. Today is {date.today().isoformat()}.
Grade each answer on its own merits with this rubric:
- correctness 0-4: are the claims true? Any false claim that matters costs heavily.
- completeness 0-3: does it cover what the question asks, including every part of it?
- groundedness 0-2: no fabrication; claims are supported by the cited sources as listed;
  an answer that admits the limits of what it knows beats a confident guess.
- usefulness 0-1: clear and directly usable by someone reading on a phone.
Length is not a virtue: do not reward padding. Ignore citation format and style.
Then say which answer is better overall, or "tie" if neither is clearly better.
Keep the rationale under 60 words."""

SCORES = {"type": "object",
          "properties": {k: {"type": "integer", "minimum": 0, "maximum": m}
                         for k, m in CRITERIA.items()},
          "required": list(CRITERIA)}
SCHEMA = {
    "type": "object",
    "properties": {"rationale": {"type": "string"}, "A": SCORES, "B": SCORES,
                   "preferred": {"type": "string", "enum": ["A", "B", "tie"]}},
    "required": ["rationale", "A", "B", "preferred"],
}


def render_ours(row):
    src = "\n".join(f"[{s['n']}] {s['title']}" + (f" — {s['section']}" if s.get("section") else "")
                    for s in row.get("sources", []))
    return f"{row.get('answer') or '(no answer)'}\n\nSources:\n{src or '(none)'}"


def render_baseline(b):
    src = "\n".join(f"[{s['n']}] {s['title']}" for s in b["sources"])
    return f"{b['answer']}\n\nSources:\n{src or '(none)'}"


def prompt_for(query, a, b, history=()):
    # Without the earlier turns, a follow-up ("Why did he build it?") cannot be graded.
    turns = "".join(f"User: {h['query']}\nAssistant: {h['answer']}\n" for h in history)
    context = (f"Earlier conversation:\n{turns}\nThe question continues this conversation. Grade each "
               "answer as the next reply in it.\n\n") if turns else ""
    return f"{context}Question: {query}\n\n=== Answer A ===\n{a}\n\n=== Answer B ===\n{b}"


def total(scores):
    return sum(scores[k] for k in CRITERIA)


def judge(row, b, model, budget, version):
    ours, base = render_ours(row), render_baseline(b)
    orders = []
    for ours_pos in ("A", "B"):
        pair = (ours, base) if ours_pos == "A" else (base, ours)
        out, raw = claude_json(prompt_for(row["query"], *pair, row.get("history", [])), SYSTEM, SCHEMA, model, "", budget)
        base_pos = "B" if ours_pos == "A" else "A"
        pref = {ours_pos: "ours", base_pos: "baseline", "tie": "tie"}[out["preferred"]]
        orders.append({"ours_pos": ours_pos, "ours": out[ours_pos], "baseline": out[base_pos],
                       "preferred": pref, "rationale": out["rationale"], **claude_meta(raw)})
    prefs = [o["preferred"] for o in orders]
    if "ours" in prefs and "baseline" not in prefs:
        outcome = "win"
    elif "baseline" in prefs and "ours" not in prefs:
        outcome = "loss"
    else:
        outcome = "tie"
    return {
        "id": row["id"],
        "category": row.get("category"),
        "ours": sum(total(o["ours"]) for o in orders) / 2,
        "baseline": sum(total(o["baseline"]) for o in orders) / 2,
        "ours_criteria": {k: sum(o["ours"][k] for o in orders) / 2 for k in CRITERIA},
        "baseline_criteria": {k: sum(o["baseline"][k] for o in orders) / 2 for k in CRITERIA},
        "outcome": outcome,
        "orders": orders,
        "requested_model": model,
        "cli_version": version,
        "prompt_version": PROMPT_VERSION,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="run id under eval/runs/")
    ap.add_argument("--subset", help="comma list of query ids and/or categories")
    ap.add_argument("--model", default="opus", help="claude --model value")
    ap.add_argument("--budget-usd", type=float, default=0.5, help="cap per judge call")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    out = RUNS / a.run / "judgments.jsonl"
    done = {j["id"] for j in read_jsonl(out)}
    keys = set(a.subset.split(",")) if a.subset else None
    todo, missing = [], []
    for row in read_jsonl(RUNS / a.run / "answers.jsonl"):
        if row["id"] in done or (keys and row["id"] not in keys and row.get("category") not in keys):
            continue
        path = BASELINES / f"{row['id']}.json"
        if path.exists():
            todo.append((row, json.loads(path.read_text())))
        else:
            missing.append(row["id"])
    if missing:
        print(f"no baseline for {len(missing)} queries: {', '.join(missing)}", file=sys.stderr)
    print(f"{len(todo)} to judge", file=sys.stderr)

    version = claude_version()
    failed = 0
    with ThreadPoolExecutor(a.jobs) as pool:
        futures = {pool.submit(judge, row, b, a.model, a.budget_usd, version): row["id"]
                   for row, b in todo}
        for f, qid in futures.items():
            try:
                j = f.result()
                append_jsonl(out, j)
                print(f"{qid}: ours={j['ours']} baseline={j['baseline']} {j['outcome']}",
                      file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"{qid}: FAILED {e}", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
