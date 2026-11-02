#!/usr/bin/env python3
"""Import answers from any other system (an app, a model, a phone run) as an eval run.

The input is JSONL, one line per question: {"id", "answer"} and optionally "record" with
"ttft_ms" and "total_ms", and "segments" in the core's citation format. The ids must come from
the query files. The script writes eval/runs/<run-id>/meta.json and answers.jsonl; then judge.py,
abstain.py, evidence.py and report.py work as for our own runs.
"""

import argparse
import json
import platform
import sys
from pathlib import Path

from common import REPO, RUNS, load_queries, now, read_jsonl, sha256


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answers", required=True, help="JSONL with id and answer per line")
    ap.add_argument("--queries", required=True, help="comma list of query files, as for run.py")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--system", required=True, help="what answered, e.g. 'AppName 1.2 on a Pixel 10'")
    ap.add_argument("--model", required=True, help="model name or file")
    ap.add_argument("--library", default="none", help="what the system searches, if anything")
    a = ap.parse_args()

    qs = {q["id"]: q for q in load_queries(a.queries)}
    out = RUNS / a.run_id
    if (out / "answers.jsonl").exists():
        sys.exit(f"{out} already has answers")
    rows, unknown = [], []
    for r in read_jsonl(Path(a.answers)):
        q = qs.get(r["id"])
        if not q:
            unknown.append(r["id"])
            continue
        rows.append({"id": q["id"], "category": q.get("category"), "query": q["query"],
                     "history": q.get("history", []), "answer": r.get("answer") or "",
                     "segments": r.get("segments"), "sources": r.get("sources", []), "record": r.get("record") or {}})
    if unknown:
        sys.exit(f"ids not in {a.queries}: {', '.join(unknown[:10])}")
    out.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": a.run_id, "started": now(), "finished": now(), "git": "external", "bin": a.system,
            "binary_sha256": "", "model": {"path": a.model, "sha256": "", "from_library": True},
            "flags": [], "cli": [], "phone": False, "think": False, "live_history": False,
            "queries": a.queries, "queries_sha256": ",".join(sha256(REPO / q) for q in a.queries.split(",")),
            "subset": None, "library": a.library, "packs": [], "imported_from": str(Path(a.answers).resolve()),
            "host": {"node": "external", "machine": platform.machine(), "cpus": 0}}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    with open(out / "answers.jsonl", "w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(f"{out}: {len(rows)} of {len(qs)} questions answered")


if __name__ == "__main__":
    main()
