#!/usr/bin/env python3
"""Answer a query set with `commonplace serve-eval`; write eval/runs/<run-id>/."""

import argparse
import json
import platform
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common import (ABLATIONS, BIN, DEFAULT_MODEL, LIBRARY, PHONE_CLI, REPO, RUNS, append_jsonl,
                    cli_flags, git_describe, library_model, library_packs, load_queries, now,
                    read_jsonl, sha256, split_evidence, write_json)


# With --live-history, these threads get their earlier turns answered by the system under test.
# Reformat and small-talk threads keep the fixed turns: their baselines rewrite or answer that text.
LIVE_CATEGORIES = {"followup", "expand", "compare_followup", "topic_switch"}


def start(cmd, log):
    return subprocess.Popen(cmd, cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=log, text=True, bufsize=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", default="eval/dev.jsonl")
    ap.add_argument("--subset", help="comma list of query ids and/or categories")
    ap.add_argument("--model", help=f"GGUF path (default: {DEFAULT_MODEL}; with --phone, the library's model pack)")
    ap.add_argument("--phone", action="store_true",
                    help="the phone's settings: " + " ".join(PHONE_CLI) + " and the library's own model; "
                    "pick the pack set with --library and the build with --bin; set ORT_DYLIB_PATH")
    ap.add_argument("--library", default=LIBRARY)
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--think", action="store_true", help="thinking mode (reasoning before the answer)")
    ap.add_argument("--bin", default=str(BIN), help="commonplace binary (default: the release build)")
    ap.add_argument("--live-history", action="store_true",
                    help="answer the earlier turns of follow-up threads first, so the history holds our own answers and sources")
    ap.add_argument("--run-id", help="default: <utc time>-<label>; an existing id resumes")
    ap.add_argument("--label", default="run")
    for f in ABLATIONS:
        ap.add_argument(f"--{f}", action="store_true")
    ap.add_argument("--cli", action="append", default=[], help="extra commonplace flag, e.g. --cli=--evidence-gap=2.5 (repeatable)")
    a = ap.parse_args()

    queries = load_queries(REPO / a.queries, a.subset)
    if not queries:
        sys.exit(f"no queries in {a.queries} (subset={a.subset})")
    run_id = a.run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{a.label}"
    out = RUNS / run_id
    out.mkdir(parents=True, exist_ok=True)
    answers = out / "answers.jsonl"
    done = {r["id"] for r in read_jsonl(answers)}

    flags = [f for f in ABLATIONS if getattr(a, f.replace("-", "_"))]
    cli = [*PHONE_CLI, *a.cli] if a.phone else a.cli
    if a.phone and not os.environ.get("ORT_DYLIB_PATH"):
        print("warning: ORT_DYLIB_PATH is not set; the quantized encoders need onnxruntime", file=sys.stderr)
    own_model = bool(a.model or not a.phone)
    model = {"path": a.model or DEFAULT_MODEL} if own_model else library_model(a.library)
    if not model:
        sys.exit(f"{a.library} has no model pack; pass --model")
    meta_path = out / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta["flags"] != flags or meta.get("cli", []) != cli or meta["model"]["path"] != model["path"] or meta["library"] != a.library or meta.get("live_history", False) != a.live_history:
            sys.exit(f"{run_id} exists with other settings; pick a new --run-id")
    else:
        meta = {
            "run_id": run_id,
            "started": now(),
            "git": git_describe(),
            "bin": str(Path(a.bin).resolve()),
            "binary_sha256": sha256(Path(a.bin)),
            "model": {"path": model["path"], "sha256": model.get("sha256") or sha256(REPO / model["path"]),
                      "from_library": not own_model},
            "flags": flags,
            "cli": cli,
            "phone": a.phone,
            "ort_dylib_path": os.environ.get("ORT_DYLIB_PATH"),
            "deep": a.deep,
            "think": a.think,
            "live_history": a.live_history,
            "queries": a.queries,
            "queries_sha256": sha256(REPO / a.queries),
            "subset": a.subset,
            "library": a.library,
            "packs": library_packs(a.library),
            "host": {"node": platform.node(), "machine": platform.machine(), "cpus": os.cpu_count()},
        }
    meta["query_ids"] = [q["id"] for q in queries]

    cmd = [a.bin, "--library", a.library, *(["--model", model["path"]] if own_model else []),
           *cli_flags(flags), *cli, "serve-eval"]
    todo = [q for q in queries if q["id"] not in done]
    print(f"{run_id}: {len(todo)} to answer, {len(done)} already done", file=sys.stderr)
    with open(out / "stderr.log", "a") as log:
        proc = start(cmd, log) if todo else None

        def ask(query, history):
            nonlocal proc
            proc.stdin.write(json.dumps({"query": query, "history": history, "deep": a.deep, "think": a.think}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            if line:
                return json.loads(line)
            err = {"error": f"serve-eval exited ({proc.wait()}); see stderr.log"}
            proc = start(cmd, log)
            return err

        for i, q in enumerate(todo, 1):
            history = q.get("history", [])
            if a.live_history and q.get("category") in LIVE_CATEGORIES:
                live = []
                for h in history:
                    r = ask(h["query"], live)
                    live.append({"query": h["query"], "answer": r.get("answer") or "", "sources": r.get("sources", [])})
                history = live
            t0 = time.monotonic()
            resp = ask(q["query"], history)
            evidence = resp.pop("evidence", None)
            if evidence is not None:
                resp["evidence"] = split_evidence(evidence, resp.get("sources", [])) or {"raw": evidence}
            row = {"id": q["id"], "category": q.get("category"), "query": q["query"],
                   "history": history, **resp,
                   "wall_ms": round((time.monotonic() - t0) * 1000, 1)}
            append_jsonl(answers, row)
            status = "ERROR " + resp["error"] if resp.get("error") else f"{row['wall_ms']:.0f} ms"
            print(f"[{i}/{len(todo)}] {q['id']}: {status}", file=sys.stderr)
        if proc:
            proc.stdin.close()
            proc.wait()

    meta["finished"] = now()
    write_json(meta_path, meta)
    print(out)


if __name__ == "__main__":
    main()
