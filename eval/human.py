#!/usr/bin/env python3
"""Check the Claude judge against a person.

`make` writes eval/runs/<run-id>/human-sheet.md: our answers to judged queries, one category after
another, and human-key.json. Write yes, partly or no after each `Correct:`; then `score` prints how
often the person agrees with the judge's correctness points (mean of both orders: 3.5 or more is
yes, 1.5 or less is no). A preference sheet would say little while the baseline wins almost every pair.
"""

import argparse
import json
import random
import re

from common import RUNS, read_jsonl


def bucket(points):
    return "yes" if points >= 3.5 else "no" if points <= 1.5 else "partly"


def make(run, n, seed):
    judged = {j["id"]: j for j in read_jsonl(RUNS / run / "judgments.jsonl")}
    rows = [r for r in read_jsonl(RUNS / run / "answers.jsonl") if r["id"] in judged]
    random.Random(seed).shuffle(rows)
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r.get("category"), []).append(r)
    picked = []
    while len(picked) < n and any(by_cat.values()):
        for c in sorted(by_cat, key=str):
            if by_cat[c] and len(picked) < n:
                picked.append(by_cat[c].pop())
    sheet = ["# Is the answer correct?\n",
             "Write yes (every claim that answers the question is right), partly (a real error or a wrong "
             "part) or no (the main answer is wrong) after each `Correct:`. Ignore style and length.\n"]
    for i, r in enumerate(picked, 1):
        sheet += [f"## {i}. {r['query']}\n", f"{r['answer']}\n", "Correct: \n"]
    key = [{"id": r["id"], "judge": bucket(judged[r["id"]]["ours_criteria"]["correctness"])} for r in picked]
    (RUNS / run / "human-sheet.md").write_text("\n".join(sheet))
    (RUNS / run / "human-key.json").write_text(json.dumps(key, indent=1))
    print(RUNS / run / "human-sheet.md")


def score(run):
    key = json.loads((RUNS / run / "human-key.json").read_text())
    marks = re.findall(r"^Correct:[ \t]*(\S*)", (RUNS / run / "human-sheet.md").read_text(), re.M)
    pairs = [(k["id"], m.lower(), k["judge"]) for k, m in zip(key, marks) if m.lower() in ("yes", "partly", "no")]
    if not pairs:
        raise SystemExit("no filled-in Correct: lines")
    agree = sum(h == j for _, h, j in pairs)
    print(f"graded {len(pairs)} / {len(key)}; same verdict as the judge: {agree} / {len(pairs)}")
    for i, h, j in pairs:
        if h != j:
            print(f"  {i}: person {h}, judge {j}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["make", "score"])
    ap.add_argument("--run", required=True, help="judged run id under eval/runs/")
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    make(a.run, a.n, a.seed) if a.cmd == "make" else score(a.run)


if __name__ == "__main__":
    main()
