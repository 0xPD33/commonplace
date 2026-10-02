#!/usr/bin/env python3
"""Per-category tables for a run: writes docs/EVAL.md, eval/runs/<id>/per_query.jsonl and
a row in eval/history.csv (replacing any earlier row for the same run)."""

import argparse
import csv
import json
import statistics
from pathlib import Path

from common import CATEGORIES, EVAL, REPO, RUNS, load_queries, now, read_jsonl

TARGET = 0.50
TARGET_CATEGORIES = ["explanation", "comparison", "multihop"]
HISTORY_FIELDS = ["date", "run_id", "smoke", "git", "model", "flags", "n", "n_judged",
                  "score_ratio", "win", "tie", "loss", "recall@10", "recall@40",
                  "card_p50_ms", "ttft_p50_ms", "total_p50_ms", "decode_tok_s", "errors"]


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=100, method="inclusive")[p - 1]


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def decode_tps(row):
    s = (row.get("record") or {}).get("synthesis") or {}
    return s["gen_tokens"] / s["decode_ms"] * 1000 if s.get("decode_ms") else None


def summarize(rows):
    judged = [r for r in rows if r.get("judgment")]
    ok = [r for r in rows if not r.get("error") and r.get("record")]
    ours = mean(r["judgment"]["ours"] for r in judged)
    base = mean(r["judgment"]["baseline"] for r in judged)
    outcomes = [r["judgment"]["outcome"] for r in judged]
    lat = {k: [r["record"][k] for r in ok if r["record"].get(k) is not None]
           for k in ("card_ms", "ttft_ms", "total_ms")}
    return {
        "n": len(rows),
        "n_judged": len(judged),
        "ours": ours,
        "baseline": base,
        "score_ratio": ours / base if judged and base else None,
        "win": outcomes.count("win"),
        "tie": outcomes.count("tie"),
        "loss": outcomes.count("loss"),
        "recall@10": mean((r.get("recall") or {}).get("recall@10") for r in rows),
        "recall@40": mean((r.get("recall") or {}).get("recall@40") for r in rows),
        "n_gold": sum(1 for r in rows if (r.get("recall") or {}).get("n_gold")),
        **{f"{k[:-3]}_p{p}": pct(v, p) for k, v in lat.items() for p in (50, 90)},
        "decode_tok_s": pct([t for t in map(decode_tps, ok) if t], 50),
        "errors": len(rows) - len(ok),
    }


def fmt(x, d=2):
    if x is None:
        return "–"
    return f"{x:.{d}f}" if isinstance(x, float) else str(x)


def ms(x):
    return "–" if x is None else f"{x / 1000:.2f}"


def tables(groups):
    q = ["| Category | n | Judged | Score ratio | W / T / L | Ours | Baseline | Gold | R@10 | R@40 |",
         "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|"]
    l = ["| Category | Card p50 / p90 (s) | TTFT p50 / p90 (s) | Total p50 / p90 (s) "
         "| Decode tok/s p50 | Errors |",
         "|---|---|---|---|---:|---:|"]
    for name, s in groups:
        q.append(f"| {name} | {s['n']} | {s['n_judged']} | {fmt(s['score_ratio'])} "
                 f"| {s['win']} / {s['tie']} / {s['loss']} | {fmt(s['ours'])} | {fmt(s['baseline'])} "
                 f"| {s['n_gold']} | {fmt(s['recall@10'])} | {fmt(s['recall@40'])} |")
        l.append(f"| {name} | {ms(s['card_p50'])} / {ms(s['card_p90'])} "
                 f"| {ms(s['ttft_p50'])} / {ms(s['ttft_p90'])} "
                 f"| {ms(s['total_p50'])} / {ms(s['total_p90'])} | {fmt(s['decode_tok_s'], 1)} "
                 f"| {s['errors']} |")
    return "\n".join(q), "\n".join(l)


def target_lines(overall, by_cat):
    lines = []
    for name, s in [("overall", overall), *[(c, by_cat.get(c)) for c in TARGET_CATEGORIES]]:
        r = s and s["score_ratio"]
        mark = "no data" if r is None else ("met" if r >= TARGET else "missed")
        lines.append(f"- {name}: {fmt(r)} (target ≥ {TARGET:.2f}): **{mark}**")
    return "\n".join(lines)


def judge_info(rows):
    for r in rows:
        j = r.get("judgment")
        if j:
            return f"{j['orders'][0]['model']} via `claude -p` {j['cli_version']}"
    return "none (no judgments)"


def baseline_info(ids):
    for i in ids:
        p = EVAL / "baselines" / f"{i}.json"
        if p.exists():
            b = json.loads(p.read_text())
            return f"{b['model']} with WebSearch/WebFetch via `claude -p` {b['cli_version']}"
    return "none (no baselines)"


def render(meta, rows, overall, groups, by_cat, smoke):
    qt, lt = tables(groups)
    rid = meta["run_id"]
    flags = " ".join(f"--{f}" for f in meta["flags"]) or "(none)"
    subset = f" --subset {meta['subset']}" if meta.get("subset") else ""
    run_flags = "".join(f" --{f}" for f in meta["flags"]) + (" --deep" if meta.get("deep") else "")
    library = meta.get("library", "data/library")
    packs = "\n".join(f"  - `{p['pack_id']}`: {p['title']}, snapshot {p['snapshot_date']}, built "
                      f"{p['build_date']}, "
                      + ", ".join(f"{v:,} {k}" for k, v in (p["counts"] or {}).items())
                      + f", manifest sha256 `{p['manifest_sha256'][:16]}…`"
                      for p in meta["packs"])
    banner = ("> **SMOKE RUN.** These numbers come from a few throwaway plumbing questions "
              "(`eval/smoke.jsonl`). They are not an eval result.\n\n" if smoke else "")
    return f"""# Evaluation

{banner}Generated by `eval/report.py` on {now()} from run `{rid}`.

## Method

- **Queries.** Our own set in `eval/queries.jsonl` (PLAN §12.1). `eval/split.py` makes a
  stratified 200-query dev split and a locked 100-query test split. The published number is
  the test number.
- **System under test.** The desktop CLI `commonplace serve-eval`, one process per run, with
  the model kept loaded.
- **Baseline.** Claude with web search, called once per query through `claude -p`. The
  answers and cited URLs live in `eval/baselines/<id>.json`.
- **Gold articles.** The baseline's Wikipedia URLs and the Wikipedia titles it lists, mapped
  to article ids in the installed packs. Hand-annotated `expected_sources` override them.
  Recall@k counts the gold articles found in the top k passages of
  `commonplace retrieve --json --k 40`. No LLM takes part.
- **Judge.** Claude grades both answers blind, as A and B, twice with the positions swapped.
  Rubric: correctness 0–4, completeness 0–3, groundedness 0–2, usefulness 0–1 (max 10).
  The two orders are averaged. A query is a win when at least one order prefers our answer
  and no order prefers the baseline; a loss is the reverse; anything else is a tie.
- **Headline.** `score_ratio = mean(ours) / mean(baseline)` over the judged queries.
- **Latency.** Taken from the `record` of each answer on the host below. PLAN §12.2 takes
  the published latency from device runs, not from this table.

## Run

- Run id: `{rid}` ({meta['started']} → {meta.get('finished', '?')})
- Git: `{meta['git']}`, binary sha256 `{meta['binary_sha256'][:16]}…`
- Model: `{meta['model']['path']}`, sha256 `{meta['model']['sha256'][:16]}…`
- Flags: {flags}; deep mode: {meta.get('deep', False)}
- Queries: `{meta['queries']}`{f" (subset `{meta['subset']}`)" if meta.get('subset') else ''}, {overall['n']} answered
- Host: {meta['host']['node']} ({meta['host']['machine']}, {meta['host']['cpus']} CPUs)
- Baseline: {baseline_info([r['id'] for r in rows])}
- Judge: {judge_info(rows)}
- Library: `{library}`, packs:
{packs}

## Results

Targets (PLAN §12.2):

{target_lines(overall, by_cat)}

### Quality and retrieval

{qt}

"Gold" counts the queries that have at least one gold article in the library; recall
averages over those queries only.

### Latency

{lt}

Per-query rows (answer, scores, recall, timings): `eval/runs/{rid}/per_query.jsonl`.

## Reproduce

Run inside the devshell from the repo root:

```sh
nix develop -c python3 eval/run.py --queries {meta['queries']}{subset} --library {library} --model {meta['model']['path']}{run_flags} --run-id {rid}
nix develop -c python3 eval/baseline.py --queries {meta['queries']}{subset}
nix develop -c python3 eval/recall.py --run {rid}
nix develop -c python3 eval/judge.py --run {rid}
nix develop -c python3 eval/report.py --run {rid}
```

The baseline skips queries that already have a file in `eval/baselines/`, so reruns reuse it.
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", help="run id under eval/runs/ (default: newest)")
    ap.add_argument("--out", default=str(REPO / "docs/EVAL.md"))
    a = ap.parse_args()

    run_dir = RUNS / a.run if a.run else max(RUNS.iterdir(), key=lambda p: p.stat().st_mtime)
    meta = json.loads((run_dir / "meta.json").read_text())
    qmeta = {q["id"]: q for q in load_queries(REPO / meta["queries"])}
    judgments = {j["id"]: j for j in read_jsonl(run_dir / "judgments.jsonl")}
    recall = {r["id"]: r for r in read_jsonl(run_dir / "recall.jsonl")}
    rows = []
    for r in read_jsonl(run_dir / "answers.jsonl"):
        r["judgment"] = judgments.get(r["id"])
        r["recall"] = recall.get(r["id"])
        rows.append(r)

    with open(run_dir / "per_query.jsonl", "w") as f:
        for r in rows:
            rec = r.get("record") or {}
            f.write(json.dumps({
                "id": r["id"], "category": r.get("category"), "query": r["query"],
                "answer": r.get("answer"), "error": r.get("error"),
                "sources": [s["title"] for s in r.get("sources", [])],
                "ours": (r["judgment"] or {}).get("ours"),
                "baseline": (r["judgment"] or {}).get("baseline"),
                "outcome": (r["judgment"] or {}).get("outcome"),
                "recall@10": (r["recall"] or {}).get("recall@10"),
                "recall@40": (r["recall"] or {}).get("recall@40"),
                "card_ms": rec.get("card_ms"), "ttft_ms": rec.get("ttft_ms"),
                "total_ms": rec.get("total_ms"), "decode_tok_s": decode_tps(r),
            }, ensure_ascii=False) + "\n")

    cats = [c for c in CATEGORIES if any(r.get("category") == c for r in rows)]
    cats += sorted({r.get("category") or "?" for r in rows} - set(cats))
    by_cat = {c: summarize([r for r in rows if (r.get("category") or "?") == c]) for c in cats}
    overall = summarize(rows)
    groups = [*by_cat.items(), ("**overall**", overall)]
    smoke = any(qmeta.get(r["id"], {}).get("smoke") for r in rows)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(render(meta, rows, overall, groups, by_cat, smoke))

    hist = EVAL / "history.csv"
    old = csv.DictReader(hist.read_text().splitlines()) if hist.exists() else []
    old = [h for h in old if h["run_id"] != meta["run_id"]]
    new = {"date": now(), "run_id": meta["run_id"], "smoke": smoke, "git": meta["git"],
           "model": Path(meta["model"]["path"]).name, "flags": " ".join(meta["flags"]),
           **{k: (round(v, 4) if isinstance(v, float) else v)
              for k, v in overall.items() if k in HISTORY_FIELDS},
           **{f"{k}_p50_ms": fmt(overall[f"{k}_p50"], 0) for k in ("card", "ttft", "total")}}
    with open(hist, "w", newline="") as f:
        w = csv.DictWriter(f, HISTORY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows([*old, new])
    print(f"{a.out}\n{run_dir / 'per_query.jsonl'}\n{hist}")


if __name__ == "__main__":
    main()
