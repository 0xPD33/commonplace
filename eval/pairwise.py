#!/usr/bin/env python3
"""Blind pairwise grading of two of our runs on the same queries with a Claude judge.

Use it to detect gains below the noise of the ratio judge in judge.py. It uses the same
rubric and prompt. Each query is graded twice with A and B swapped; the judge sees neither run
id. Results go to eval/runs/<run-a>/pairwise-vs-<run-b>.jsonl (resumes), then a summary:
win / tie / loss of run A, an exact sign test on the non-tied queries, and the mean score
difference with a bootstrap 95% interval. Identical answers are a tie without a judge call.
A follow-up question is graded after the fixed earlier turns of the query file.
"""

import argparse
import json
import random
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from math import comb

from common import REPO, RUNS, append_jsonl, claude_json, claude_meta, claude_version, load_queries, read_jsonl
from judge import CRITERIA, PROMPT_VERSION, SCHEMA, SYSTEM, prompt_for, render_ours, total


def compare(a, b, history, model, budget, version):
    sa, sb = render_ours(a), render_ours(b)
    base = {"id": a["id"], "category": a.get("category"), "prompt_version": PROMPT_VERSION,
            "requested_model": model, "cli_version": version}
    if sa == sb:
        return {**base, "a": None, "b": None, "diff": 0.0, "outcome": "tie", "identical": True, "orders": []}
    orders = []
    for a_pos in ("A", "B"):
        pair = (sa, sb) if a_pos == "A" else (sb, sa)
        out, raw = claude_json(prompt_for(a["query"], *pair, history), SYSTEM, SCHEMA, model, "", budget)
        b_pos = "B" if a_pos == "A" else "A"
        pref = {a_pos: "a", b_pos: "b", "tie": "tie"}[out["preferred"]]
        orders.append({"a_pos": a_pos, "a": out[a_pos], "b": out[b_pos], "preferred": pref,
                       "rationale": out["rationale"], **claude_meta(raw)})
    prefs = [o["preferred"] for o in orders]
    outcome = "win" if "a" in prefs and "b" not in prefs else "loss" if "b" in prefs and "a" not in prefs else "tie"
    mean_a = sum(total(o["a"]) for o in orders) / 2
    mean_b = sum(total(o["b"]) for o in orders) / 2
    return {**base, "a": mean_a, "b": mean_b, "diff": mean_a - mean_b, "outcome": outcome,
            "identical": False,
            "a_criteria": {k: sum(o["a"][k] for o in orders) / 2 for k in CRITERIA},
            "b_criteria": {k: sum(o["b"][k] for o in orders) / 2 for k in CRITERIA},
            "orders": orders}


def sign_test(wins, losses):
    """Two-sided exact p-value that wins and losses are equally likely; ties are dropped."""
    n = wins + losses
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(wins, losses) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def bootstrap_ci(xs, n=10000, seed=0):
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def summarize(rows):
    w = sum(r["outcome"] == "win" for r in rows)
    l = sum(r["outcome"] == "loss" for r in rows)
    diffs = [r["diff"] for r in rows]
    judged = [r for r in rows if not r.get("identical")]
    lo, hi = bootstrap_ci(diffs) if diffs else (0.0, 0.0)
    return {"n": len(rows), "win": w, "tie": len(rows) - w - l, "loss": l, "sign_p": sign_test(w, l),
            "mean_a": statistics.fmean(r["a"] for r in judged) if judged else None,
            "mean_b": statistics.fmean(r["b"] for r in judged) if judged else None,
            "mean_diff": statistics.fmean(diffs) if diffs else None, "diff_ci95": [lo, hi],
            "identical": sum(r.get("identical", False) for r in rows),
            "cost_usd": sum(o.get("cost_usd") or 0 for r in rows for o in r["orders"])}


def fmt(x):
    return "-" if x is None else f"{x:.2f}"


def show(name, s):
    return (f"{name}: n={s['n']} A wins {s['win']} / ties {s['tie']} / A losses {s['loss']}  "
            f"sign-test p={s['sign_p']:.3f}  mean score A {fmt(s['mean_a'])} B {fmt(s['mean_b'])} (judged)  "
            f"diff {s['mean_diff']:+.2f} [{s['diff_ci95'][0]:+.2f}, {s['diff_ci95'][1]:+.2f}]"
            f"  identical {s['identical']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True, help="run id of variant A")
    ap.add_argument("--b", required=True, help="run id of variant B")
    ap.add_argument("--subset", help="comma list of query ids and/or categories")
    ap.add_argument("--model", default="opus", help="claude --model value")
    ap.add_argument("--budget-usd", type=float, default=0.5, help="cap per judge call")
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()

    out = RUNS / a.a / f"pairwise-vs-{a.b}.jsonl"
    meta_a = json.loads((RUNS / a.a / "meta.json").read_text())
    history = {q["id"]: q.get("history", []) for q in load_queries(REPO / meta_a["queries"])}
    rows_b = {r["id"]: r for r in read_jsonl(RUNS / a.b / "answers.jsonl")}
    keys = set(a.subset.split(",")) if a.subset else None
    done = {j["id"] for j in read_jsonl(out)}
    todo, skipped = [], []
    for ra in read_jsonl(RUNS / a.a / "answers.jsonl"):
        if ra["id"] in done or (keys and ra["id"] not in keys and ra.get("category") not in keys):
            continue
        rb = rows_b.get(ra["id"])
        if rb is None or ra.get("error") or rb.get("error"):
            skipped.append(ra["id"])
        else:
            todo.append((ra, rb))
    if skipped:
        print(f"no usable pair for {len(skipped)} queries: {', '.join(skipped)}", file=sys.stderr)
    print(f"{len(todo)} to judge", file=sys.stderr)

    version = claude_version()
    failed = 0
    with ThreadPoolExecutor(a.jobs) as pool:
        futures = {pool.submit(compare, ra, rb, history.get(ra["id"], []), a.model, a.budget_usd, version): ra["id"]
                   for ra, rb in todo}
        for f, qid in futures.items():
            try:
                j = f.result()
                append_jsonl(out, j)
                print(f"{qid}: A={j['a']} B={j['b']} {j['outcome']}", file=sys.stderr)
            except Exception as e:
                failed += 1
                print(f"{qid}: FAILED {e}", file=sys.stderr)

    rows = [j for j in read_jsonl(out) if not keys or j["id"] in keys or j.get("category") in keys]
    if rows:
        print(show("overall", summarize(rows)))
        for c in sorted({r["category"] for r in rows}):
            s = summarize([r for r in rows if r["category"] == c])
            print(f"  {c}: n={s['n']} {s['win']}/{s['tie']}/{s['loss']} diff {s['mean_diff']:+.2f}")
        print(f"judge cost ${summarize(rows)['cost_usd']:.2f}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
