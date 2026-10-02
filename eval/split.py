#!/usr/bin/env python3
"""Stratified dev/test split of eval/queries.jsonl into eval/dev.jsonl and eval/test.jsonl.

The test split is locked (PLAN §12.1): the script refuses to run once eval/test.jsonl exists.
"""

import argparse
import json
import random
import sys
from collections import defaultdict

from common import EVAL, read_jsonl


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", default=str(EVAL / "queries.jsonl"))
    ap.add_argument("--test-size", type=int, default=100)
    ap.add_argument("--seed", type=int, default=12)
    a = ap.parse_args()

    test_path, dev_path = EVAL / "test.jsonl", EVAL / "dev.jsonl"
    if test_path.exists():
        sys.exit(f"{test_path} exists and is locked; delete it by hand only if you mean to re-split")
    qs = [q for q in read_jsonl(a.queries) if not q.get("smoke")]
    if len(qs) <= a.test_size:
        sys.exit(f"only {len(qs)} queries; need more than --test-size {a.test_size}")

    by_cat = defaultdict(list)
    for q in qs:
        by_cat[q["category"]].append(q)
    # Largest-remainder allocation keeps every category's test share proportional.
    exact = {c: len(v) * a.test_size / len(qs) for c, v in by_cat.items()}
    alloc = {c: int(x) for c, x in exact.items()}
    for c in sorted(exact, key=lambda c: exact[c] - alloc[c], reverse=True)[:a.test_size - sum(alloc.values())]:
        alloc[c] += 1

    rng = random.Random(a.seed)
    test_ids = set()
    for c in sorted(by_cat):
        test_ids |= {q["id"] for q in rng.sample(by_cat[c], alloc[c])}
    for path, keep in ((test_path, True), (dev_path, False)):
        with open(path, "w") as f:
            for q in qs:
                if (q["id"] in test_ids) == keep:
                    f.write(json.dumps(q, ensure_ascii=False) + "\n")
    for c in sorted(by_cat):
        print(f"{c}: {len(by_cat[c]) - alloc[c]} dev / {alloc[c]} test")
    print(f"total: {len(qs) - len(test_ids)} dev / {len(test_ids)} test -> {dev_path}, {test_path}")


if __name__ == "__main__":
    main()
