#!/usr/bin/env python3
"""Per-category tables for a run: writes docs/EVAL.md, eval/runs/<id>/per_query.jsonl and
a row in eval/history.csv (replacing any earlier row for the same run)."""

import argparse
import csv
import json
import random
import re
import statistics
import sys
from pathlib import Path

from common import BASELINES, CATEGORIES, EVAL, PHONE_CLI, REPO, RUNS, load_queries, now, portable, read_jsonl
from judge import CRITERIA
from pairwise import summarize as pairwise_summary

TARGET = 0.50
TARGET_CATEGORIES = ["explanation", "comparison", "multihop"]
HISTORY_FIELDS = ["date", "run_id", "smoke", "git", "model", "flags", "n", "n_judged",
                  "score_ratio", "win", "tie", "loss", "recall@10", "recall@40", "recall@ev",
                  "card_p50_ms", "ttft_p50_ms", "total_p50_ms", "decode_tok_s", "errors",
                  "unverified_rate", "uncited_rate", "cites", "expect_rate", "abstain_rate"]


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


SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def sentences(segments):
    """Answer sentences from the citation segments, each with cited/unverified flags.
    The desktop CLI writes `Text`/`Cite`/`Unverified`, the phone app lowercase keys."""
    out, cur = [], {"text": "", "cited": False, "unverified": False}

    def close():
        nonlocal cur
        if any(c.isalnum() for c in cur["text"]):
            out.append(cur)
        cur = {"text": "", "cited": False, "unverified": False}

    for seg in segments or []:
        (kind, val), = seg.items()
        kind = kind.lower()
        if kind == "cite":
            cur["cited"] = True
            continue
        pieces = SENTENCE_END.split(val)
        for i, piece in enumerate(pieces):
            cur["text"] += piece
            cur["unverified"] |= kind == "unverified" and bool(piece.strip())
            if i < len(pieces) - 1:
                close()
    close()
    return out


def citation_stats(row):
    ss = sentences(row.get("segments"))
    cites = {v for seg in row.get("segments") or [] for k, v in seg.items() if k.lower() == "cite"}
    return {"sentences": len(ss), "unverified": sum(s["unverified"] for s in ss),
            "uncited": sum(not s["cited"] for s in ss), "cites": len(cites)}


NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?:\s*(thousand|million|billion|trillion))?", re.I)
SCALE = {"thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}


def numbers(text):
    for m in NUMBER.finditer(text or ""):
        digits = m.group(0).split()[0].replace(",", "")
        try:
            yield float(digits) * SCALE.get((m.group(1) or "").lower(), 1)
        except ValueError:
            pass


def expect_ok(answer, expect):
    """Deterministic check of a query's `expect`: a list of strings (any one, case-insensitive, in the
    first sentence, so "China …; India is second" fails for India) or
    {"text": [...], "sentences": n} to search the first n sentences (multi-hop answers name the
    last hop second), or {"number": x or [x, ...], "tol": relative tolerance, default 0.02}; a list of numbers
    accepts any one, for questions with two common readings. None when the query has no `expect`.
    The app has a Kotlin copy in ui/DiagnosticsScreen.kt (`expectOk`): change both together."""
    if not expect:
        return None
    if isinstance(expect, dict) and "number" in expect:
        xs, tol = expect["number"], expect.get("tol", 0.02)
        xs = xs if isinstance(xs, list) else [xs]
        return any(abs(n - x) <= abs(x) * tol for n in numbers(answer) for x in xs)
    texts, k = (expect["text"], expect["sentences"]) if isinstance(expect, dict) else (expect, 1)
    head = " ".join([p for p in SENTENCE_END.split(answer or "") if p.strip()][:k]).lower()
    return any(s.lower() in head for s in texts)


def ratio_ci(judged, n=2000, seed=0):
    """Bootstrap 95% interval of mean(ours) / mean(baseline) over the judged queries."""
    if len(judged) < 2:
        return None
    pairs = [(r["judgment"]["ours"], r["judgment"]["baseline"]) for r in judged]
    rng = random.Random(seed)
    ratios = []
    for _ in range(n):
        sample = rng.choices(pairs, k=len(pairs))
        ratios.append(sum(o for o, _ in sample) / sum(b for _, b in sample))
    ratios.sort()
    return ratios[int(0.025 * n)], ratios[int(0.975 * n)]


def summarize(rows):
    judged = [r for r in rows if r.get("judgment")]
    ok = [r for r in rows if not r.get("error") and r.get("record")]
    ours = mean(r["judgment"]["ours"] for r in judged)
    base = mean(r["judgment"]["baseline"] for r in judged)
    outcomes = [r["judgment"]["outcome"] for r in judged]
    labels = [r["evidence_label"] for r in rows if r.get("evidence_label")]
    suff = [x["sufficiency"] for x in labels]
    losses = [r["evidence_label"]["sufficiency"] for r in judged
              if r["judgment"]["outcome"] == "loss" and r.get("evidence_label")]
    lat = {k: [r["record"][k] for r in ok if r["record"].get(k) is not None]
           for k in ("card_ms", "ttft_ms", "total_ms")}
    cs = [citation_stats(r) for r in rows if r.get("segments")]
    n_sent = sum(c["sentences"] for c in cs)
    checks = [r["expect_ok"] for r in rows if r.get("expect_ok") is not None]
    abst = [r["abstain"] for r in rows if r.get("abstain")]
    return {
        "abstain_n": len(abst),
        "abstain_pass": sum(x["pass"] for x in abst),
        "abstain_rate": sum(x["pass"] for x in abst) / len(abst) if abst else None,
        "invented": sum(x["invented"] for x in abst),
        "unverified_rate": sum(c["unverified"] for c in cs) / n_sent if n_sent else None,
        "uncited_rate": sum(c["uncited"] for c in cs) / n_sent if n_sent else None,
        "cites": mean(c["cites"] for c in cs),
        "expect_n": len(checks),
        "expect_pass": sum(checks),
        "expect_rate": sum(checks) / len(checks) if checks else None,
        "n": len(rows),
        "n_judged": len(judged),
        "ours": ours,
        "baseline": base,
        "score_ratio": ours / base if judged and base else None,
        "ratio_ci": ratio_ci(judged),
        "criteria": {k: (mean(r["judgment"]["ours_criteria"][k] for r in judged),
                         mean(r["judgment"]["baseline_criteria"][k] for r in judged)) for k in CRITERIA},
        "win": outcomes.count("win"),
        "tie": outcomes.count("tie"),
        "loss": outcomes.count("loss"),
        "recall@10": mean((r.get("recall") or {}).get("recall@10") for r in rows),
        "recall@40": mean((r.get("recall") or {}).get("recall@40") for r in rows),
        "recall@ev": mean((r.get("recall") or {}).get("recall@ev") for r in rows),
        "recall_wiki@10": mean((r.get("recall") or {}).get("recall_wiki@10") for r in rows),
        "recall_wiki@40": mean((r.get("recall") or {}).get("recall_wiki@40") for r in rows),
        "n_gold": sum(1 for r in rows if (r.get("recall") or {}).get("n_gold")),
        "ev_relevant": mean(len({n for n in x["relevant"] if 1 <= n <= x["n_sources"]}) / x["n_sources"]
                            for x in labels),
        "ev_full": suff.count("full"), "ev_partial": suff.count("partial"), "ev_none": suff.count("none"),
        "loss_full_evidence": losses.count("full"), "loss_thin_evidence": len(losses) - losses.count("full"),
        **{f"{k[:-3]}_p{p}": pct(v, p) for k, v in lat.items() for p in (50, 90)},
        "decode_tok_s": pct([t for t in map(decode_tps, ok) if t], 50),
        "errors": len(rows) - len(ok),
    }


def fmt(x, d=2):
    if x is None:
        return "–"
    return f"{x:.{d}f}" if isinstance(x, float) else str(x)


def evidence_cell(s):
    n = s["ev_full"] + s["ev_partial"] + s["ev_none"]
    return f"{s['ev_full']} / {s['ev_partial']} / {s['ev_none']}" if n else "–"


def ms(x):
    return "–" if x is None else f"{x / 1000:.2f}"


def pct_cell(x):
    return "–" if x is None else f"{x:.1%}"


def tables(groups):
    q = ["| Category | n | Judged | Score ratio | W / T / L | Ours | Baseline | Gold | R@10 | R@40 | R@ev | Evidence F / P / N | Relevant sources |",
         "|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---:|"]
    l = ["| Category | Card p50 / p90 (s) | TTFT p50 / p90 (s) | Total p50 / p90 (s) "
         "| Decode tok/s p50 | Errors |",
         "|---|---|---|---|---:|---:|"]
    c = ["| Category | Unverified sentences | Uncited sentences | Sources cited per answer | Expected answer found "
         "| Abstention pass (invented) |",
         "|---|---:|---:|---:|---:|---:|"]
    for name, s in groups:
        expect = f"{s['expect_pass']} / {s['expect_n']}" if s["expect_n"] else "–"
        abst = f"{s['abstain_pass']} / {s['abstain_n']} ({s['invented']})" if s["abstain_n"] else "–"
        c.append(f"| {name} | {pct_cell(s['unverified_rate'])} | {pct_cell(s['uncited_rate'])} "
                 f"| {fmt(s['cites'], 1)} | {expect} | {abst} |")
        ci = f" [{s['ratio_ci'][0]:.2f}, {s['ratio_ci'][1]:.2f}]" if s["ratio_ci"] else ""
        q.append(f"| {name} | {s['n']} | {s['n_judged']} | {fmt(s['score_ratio'])}{ci} "
                 f"| {s['win']} / {s['tie']} / {s['loss']} | {fmt(s['ours'])} | {fmt(s['baseline'])} "
                 f"| {s['n_gold']} | {fmt(s['recall@10'])} | {fmt(s['recall@40'])} | {fmt(s['recall@ev'])} "
                 f"| {evidence_cell(s)} | {fmt(s['ev_relevant'])} |")
        l.append(f"| {name} | {ms(s['card_p50'])} / {ms(s['card_p90'])} "
                 f"| {ms(s['ttft_p50'])} / {ms(s['ttft_p90'])} "
                 f"| {ms(s['total_p50'])} / {ms(s['total_p90'])} | {fmt(s['decode_tok_s'], 1)} "
                 f"| {s['errors']} |")
    g = ["| Category | " + " | ".join(f"{k} (max {m})" for k, m in CRITERIA.items()) + " |",
         "|---|" + "---:|" * len(CRITERIA)]
    for name, s in groups:
        if s["n_judged"]:
            g.append(f"| {name} | " + " | ".join(f"{s['criteria'][k][0]:.2f} / {s['criteria'][k][1]:.2f}"
                                               for k in CRITERIA) + " |")
    return "\n".join(q), "\n".join(l), "\n".join(c), "\n".join(g)


def target_lines(overall, by_cat):
    lines = []
    for name, s in [("overall", overall), *[(c, by_cat.get(c)) for c in TARGET_CATEGORIES]]:
        r = s and s["score_ratio"]
        mark = "no data" if r is None else ("met" if r >= TARGET else "missed")
        lines.append(f"- {name}: {fmt(r)} (target ≥ {TARGET:.2f}): **{mark}**")
    return "\n".join(lines)


def evidence_note(s):
    if not (s["loss_full_evidence"] or s["loss_thin_evidence"]):
        return ""
    return (f" Of the {s['loss']} lost queries, {s['loss_full_evidence']} had full evidence (the generator "
            f"failed) and {s['loss_thin_evidence']} had partial or no evidence (retrieval failed).")


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


def baseline_words(qid):
    p = BASELINES / f"{qid}.json"
    return len((json.loads(p.read_text()).get("answer") or "").split()) if p.exists() else None


def findings(rows, qmeta, s, by_cat):
    """Computed lines for the top of the report: where the points go and how far to trust them."""
    judged = [r for r in rows if r.get("judgment")]
    out = []
    if judged:
        ci = s["ratio_ci"]
        out.append(f"- Score ratio **{s['score_ratio']:.2f}**" + (f" (95% interval {ci[0]:.2f}–{ci[1]:.2f})" if ci else "")
                   + f" over {len(judged)} judged queries.")
        k = max(CRITERIA, key=lambda k: s["criteria"][k][1] - s["criteria"][k][0])
        out.append(f"- Largest rubric gap: **{k}**, {s['criteria'][k][0]:.2f} vs {s['criteria'][k][1]:.2f} of {CRITERIA[k]}.")
        cats = [(c, x) for c, x in by_cat.items() if x["n_judged"] >= 5]
        if cats:
            c, x = min(cats, key=lambda cx: cx[1]["score_ratio"])
            out.append(f"- Weakest category with 5 or more judged queries: **{c}**, {x['score_ratio']:.2f}.")
        weak = [(r.get("evidence_label") or {}).get("sufficiency") for r in judged if r["judgment"]["ours"] < 5]
        if any(weak):
            out.append(f"- {len(weak)} answers score below 5 of 10: **{sum(e in ('partial', 'none') for e in weak)} retrieval "
                       f"failures** (evidence partial or none) and **{weak.count('full')} generator failures** (evidence full).")
        score = {r["id"]: r["judgment"]["ours"] for r in judged}
        pairs = [(score[i], score[q["baseline"]]) for i, q in qmeta.items()
                 if q.get("variant") and i in score and q["baseline"] in score]
        if pairs:
            out.append(f"- Misspelled or spoken wording: {mean(p[0] for p in pairs):.2f} vs "
                       f"{mean(p[1] for p in pairs):.2f} for the clean wording (n = {len(pairs)}).")
        words = [(len((r.get("answer") or "").split()), baseline_words(qmeta.get(r["id"], {}).get("baseline", r["id"])),
                  r["judgment"]["ours"]) for r in judged]
        if len(words) > 2 and len({w[0] for w in words}) > 1:
            out.append(f"- Answer length p50: {pct([w[0] for w in words], 50):.0f} words vs "
                       f"{pct([w[1] for w in words if w[1]], 50):.0f} for the baseline. Correlation of our length "
                       f"with our score: r = {statistics.correlation([w[0] for w in words], [w[2] for w in words]):+.2f}.")
        both = [r["judgment"]["orders"] for r in judged if len(r["judgment"]["orders"]) == 2]
        agree = sum(o[0]["preferred"] == o[1]["preferred"] for o in both)
        out.append(f"- The two judge orders prefer the same answer in {agree} / {len(both)} queries.")
    checks = ([f"expected answer {s['expect_pass']} / {s['expect_n']}"] if s["expect_n"] else []) \
        + ([f"abstention {s['abstain_pass']} / {s['abstain_n']} ({s['invented']} invented)"] if s["abstain_n"] else []) \
        + [f"unverified sentences {pct_cell(s['unverified_rate'])}"]
    out.append(f"- Checks: {', '.join(checks)}.")
    cost = (sum(o.get("cost_usd") or 0 for r in judged for o in r["judgment"]["orders"])
            + sum((r.get(k) or {}).get("cost_usd") or 0 for r in rows for k in ("evidence_label", "abstain")))
    if cost:
        out.append(f"- Grading cost: ${cost:.2f} (judge, evidence and abstention calls; baselines are cached).")
    return "\n".join(out)


def look_at(rows, n=10):
    """The answers worth reading first: failed checks, then the lowest judge scores."""
    lines = ["| Query | Category | Problem | Detail |", "|---|---|---|---|"]
    for r in rows:
        if r.get("expect_ok") is False:
            lines.append(f"| `{r['id']}` | {r.get('category')} | expected answer missing | {cell(r.get('answer'), 120)} |")
        if r.get("abstain") and not r["abstain"]["pass"]:
            lines.append(f"| `{r['id']}` | {r.get('category')} | abstention failed"
                         f"{' (invented)' if r['abstain']['invented'] else ''} | {cell(r['abstain']['reason'], 160)} |")
    judged = sorted((r for r in rows if r.get("judgment")), key=lambda r: r["judgment"]["ours"])[:n]
    for r in judged:
        j = r["judgment"]
        o = j["orders"][0]
        ev = (r.get("evidence_label") or {}).get("sufficiency")
        why = "retrieval" if ev in ("partial", "none") else "generator" if ev == "full" else "unlabeled"
        lines.append(f"| `{r['id']}` | {r.get('category')} | score {j['ours']:.1f} vs {j['baseline']:.1f} ({why}) "
                     f"| ours = {o['ours_pos']}: {cell(o['rationale'], 220)} |")
    return "\n".join(lines)


def cell(text, limit):
    t = " ".join((text or "").split()).replace("|", "/")
    return t if len(t) <= limit else t[:limit - 1] + "…"


def shown(paths):
    """Older runs recorded full local paths. The report publishes them as `portable` does; relative ones stay."""
    return ",".join(portable(p) if Path(p).is_absolute() else p for p in paths.split(","))


def render(meta, rows, overall, groups, by_cat, smoke, summary):
    qt, lt, ct, gt = tables(groups)
    rid = meta["run_id"]
    model_path, bin_path, queries = shown(meta["model"]["path"]), shown(meta.get("bin") or ""), shown(meta["queries"])
    flags = " ".join(f"--{f}" for f in meta["flags"]) or "(none)"
    subset = f" --subset {meta['subset']}" if meta.get("subset") else ""
    phone = meta.get("phone")
    run_flags = ("".join(f" --{f}" for f in meta["flags"])
                 + (" --live-history" if meta.get("live_history") else "") + (" --phone" if phone else ""))
    cli_args = "".join(f" --cli={c}" for c in meta.get("cli", []) if not (phone and c in PHONE_CLI))
    model_arg = "" if meta["model"].get("from_library") else f" --model {model_path}"
    bin_arg = f" --bin {bin_path}" if bin_path else ""
    cli_text = " ".join(meta.get("cli", [])) or "(none)"
    library = shown(meta.get("library", "data/library"))
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

- **Queries.** Our own seed questions in `eval/queries.jsonl`, in nine categories, split into
  `dev.jsonl` (for tuning) and `test.jsonl` (locked: run it to report, never to tune). Beside them:
  `canary.jsonl` (12 easy questions with a fixed answer), `abstention.jsonl` (15 questions the
  library cannot answer, 15 with a false premise), `robust.jsonl` (20 misspelled or spoken versions
  of seeds) and `recent.jsonl` (20 events of 2025 and 2026 that the snapshot covers and the model
  does not know). The multi-turn threads are in `eval/conversation.jsonl` and
  `eval/conversation-test.jsonl` (locked).
- **System under test.** The desktop CLI `commonplace serve-eval`, one process per run, with
  the model kept loaded.
- **Baseline.** Claude with web search, called once per query through `claude -p`. The
  answers and cited URLs live in `eval/baselines/<id>.json`.
- **Gold articles.** The baseline's Wikipedia URLs and the Wikipedia titles it lists, plus the
  baseline URLs that open a page of another pack (Wikivoyage, Stack Exchange, Arch Wiki, ...),
  mapped to articles in the installed packs. Hand-annotated `expected_sources` override them.
  Recall@k counts the gold articles found in the top k passages of
  `commonplace retrieve --json` (@10 with the run's own `--fuse-keep`, @40 with a pool of 40).
  R@ev counts the gold articles among the sources the model saw. No LLM takes part.
- **Judge.** Claude grades both answers blind, as A and B, twice with the positions swapped.
  Rubric: correctness 0–4, completeness 0–3, groundedness 0–2, usefulness 0–1 (max 10).
  The two orders are averaged. A query is a win when at least one order prefers our answer
  and no order prefers the baseline; a loss is the reverse; anything else is a tie.
- **Headline.** `score_ratio = mean(ours) / mean(baseline)` over the judged queries.
- **Latency.** Taken from the `record` of each answer on the host below. These are desktop
  numbers. The phone numbers are in `docs/BENCHMARKS.md`.

## Run

- Run id: `{rid}` ({meta['started']} → {meta.get('finished', '?')})
- Git: `{meta['git']}`, binary sha256 `{meta['binary_sha256'][:16]}…`
- Model: `{model_path}`, sha256 `{meta['model']['sha256'][:16]}…`
- Binary: `{bin_path or '?'}`
- Flags: {flags}; extra CLI flags: {cli_text}; phone preset: {bool(phone)}
- Queries: `{queries}`{f" (subset `{meta['subset']}`)" if meta.get('subset') else ''}, {overall['n']} answered
- Host: {meta['host']['node']} ({meta['host']['machine']}, {meta['host']['cpus']} CPUs)
- Baseline: {baseline_info([r['id'] for r in rows])}
- Judge: {judge_info(rows)}
- Library: `{library}`, packs:
{packs}

## Results

Targets (score ratio of at least 0.50):

{target_lines(overall, by_cat)}

### Summary

{summary}

### Quality and retrieval

{qt}

The brackets after a score ratio give its bootstrap 95% interval over the judged queries.
"Gold" counts the queries that have at least one gold article in the library; recall
averages over those queries only. With Wikipedia-only gold (the earlier method) the overall
recall is R@10 {fmt(overall['recall_wiki@10'])} and R@40 {fmt(overall['recall_wiki@40'])}.
"Evidence F / P / N" counts the queries whose evidence a Claude judge rates as full, partial or no
answer to the question (`eval/evidence.py`). "Relevant sources" is the share of the sources that
the judge marks as carrying facts the answer needs. Both count passages from any pack.{evidence_note(overall)}

### Where the points go

Mean judge points per rubric item, ours / baseline.

{gt}

### Citations and checks

{ct}

These columns need no LLM. "Unverified" counts the answer sentences that the core's citation check
flags: a number missing from the evidence, or no citation and too few evidence words
(`citations.rs`, `engine.rs`). "Uncited" counts the sentences without a citation. "Expected answer
found" checks the queries that carry an `expect` field: one of its strings, or a number within its
tolerance, must appear in the answer. "Abstention pass" comes from a Claude judge (`eval/abstain.py`)
on the questions that the library cannot answer or that carry a false premise; "invented" counts
the answers that state made-up specifics.

### Answers to look at

Failed checks first, then the 10 lowest judge scores. "Retrieval" means the evidence was partial or
missing (`evidence.py`); "generator" means the evidence was enough and the answer still lost.

{look_at(rows)}

### Latency

{lt}

Per-query rows (answer, scores, recall, timings): `eval/runs/{rid}/per_query.jsonl`.

## Reproduce

Run inside the devshell from the repo root:

```sh
nix develop -c python3 eval/run.py --queries {queries}{subset} --library {library}{model_arg}{run_flags}{cli_args}{bin_arg} --run-id {rid}
nix develop -c python3 eval/baseline.py --queries {queries}{subset}
nix develop -c python3 eval/recall.py --run {rid}
nix develop -c python3 eval/judge.py --run {rid}
nix develop -c python3 eval/evidence.py --run {rid}
nix develop -c python3 eval/report.py --run {rid}
```

The baseline skips queries that already have a file in `eval/baselines/`, so reruns reuse it.
"""


def load_run(run_dir):
    meta = json.loads((run_dir / "meta.json").read_text())
    qmeta = {q["id"]: q for q in load_queries(REPO / meta["queries"])}
    judgments = {j["id"]: j for j in read_jsonl(run_dir / "judgments.jsonl")}
    recall = {r["id"]: r for r in read_jsonl(run_dir / "recall.jsonl")}
    labels = {r["id"]: r for r in read_jsonl(run_dir / "evidence.jsonl")}
    abstain = {r["id"]: r for r in read_jsonl(run_dir / "abstain.jsonl")}
    rows = []
    for r in read_jsonl(run_dir / "answers.jsonl"):
        r["judgment"] = judgments.get(r["id"])
        r["recall"] = recall.get(r["id"])
        r["evidence_label"] = labels.get(r["id"])
        r["abstain"] = abstain.get(r["id"])
        r["expect_ok"] = expect_ok(r.get("answer"), qmeta.get(r["id"], {}).get("expect"))
        rows.append(r)
    return meta, qmeta, rows


def systems(run_ids):
    """One row per run, all restricted to the queries that every run answered."""
    runs = [(rid, *load_run(RUNS / rid)[::2]) for rid in run_ids]
    ids = set.intersection(*({r["id"] for r in rows} for _, _, rows in runs))
    lines = ["| Run | Model | Library | Score ratio | Judged | Expected answer | Abstention (invented) "
             "| Unverified | First word p50 (s) |", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for rid, meta, rows in runs:
        s = summarize([r for r in rows if r["id"] in ids])
        ci = f" [{s['ratio_ci'][0]:.2f}, {s['ratio_ci'][1]:.2f}]" if s["ratio_ci"] else ""
        lib = "none (closed book)" if "--closed-book" in meta.get("cli", []) else "yes"
        lines.append(f"| `{rid}` | {Path(meta['model']['path']).name} | {lib} | {fmt(s['score_ratio'])}{ci} "
                     f"| {s['n_judged']} | {s['expect_pass']} / {s['expect_n']} "
                     f"| {s['abstain_pass']} / {s['abstain_n']} ({s['invented']}) | {pct_cell(s['unverified_rate'])} "
                     f"| {ms(s['ttft_p50'])} |")
    return (f"## Systems\n\nThe same {len(ids)} questions for each run. A closed-book run is the same model "
            "with no library: the gap to it is what retrieval adds. First word times are desktop times.\n\n"
            + "\n".join(lines) + "\n")


GATE_UNVERIFIED_SLACK = 0.01


def gate(run_dirs, ref_ids):
    """Release checks of a candidate's rolls against the reference's rolls on the same query files. Counts pool over
    the rolls: any prompt edit re-rolls about 80% of the answers, so one roll cannot judge. Returns (passed, markdown)."""
    rolls = [load_run(d)[2] for d in run_dirs]
    ids = {r["id"] for r in rolls[0]}
    refs = [[r for r in load_run(RUNS / ref)[2] if r["id"] in ids] for ref in ref_ids]
    rows, ref_rows = sum(rolls, []), sum(refs, [])
    canary = [r for r in rows if r.get("category") == "canary"]
    rest, ref_rest = ([r for r in rs if r.get("category") != "canary"] for rs in (rows, ref_rows))
    new, ref = summarize(rest), summarize(ref_rest)
    checks = []

    errors = sum(1 for r in rows if r.get("error"))
    checks.append(("No errors", errors == 0, f"{errors} errors"))
    passed = sum(bool(r["expect_ok"]) for r in canary)
    checks.append(("Canary", bool(canary) and passed == len(canary), f"{passed} / {len(canary)}"))
    if new["unverified_rate"] is not None and ref["unverified_rate"] is not None:
        checks.append(("Unverified sentences", new["unverified_rate"] <= ref["unverified_rate"] + GATE_UNVERIFIED_SLACK,
                       f"{new['unverified_rate']:.1%} vs {ref['unverified_rate']:.1%} (limit +{GATE_UNVERIFIED_SLACK:.0%})"))
    if new["expect_n"]:
        checks.append(("Expected answers", new["expect_pass"] >= ref["expect_pass"],
                       f"{new['expect_pass']} / {new['expect_n']} vs {ref['expect_pass']} / {ref['expect_n']}"))
    if new["abstain_n"]:
        checks.append(("Abstention", new["abstain_pass"] >= ref["abstain_pass"] - len(rolls),
                       f"{new['abstain_pass']} / {new['abstain_n']} vs {ref['abstain_pass']} / {ref['abstain_n']} (limit -{len(rolls)})"))
    # Quality: the blind pairwise judge on the locked test split (all pairs if there is no split).
    test = {q["id"] for q in read_jsonl(EVAL / "test.jsonl")}
    pws = [[p for p in read_jsonl(d / f"pairwise-vs-{ref}.jsonl") if not test or p["id"] in test]
           for d, ref in zip(run_dirs, ref_ids)]
    if all(pws):
        s = pairwise_summary(per_question(pws))
        worse = s["loss"] > s["win"] and s["sign_p"] < 0.05
        checks.append(("Pairwise quality", not worse,
                       f"{s['win']} / {s['tie']} / {s['loss']}, sign test p = {s['sign_p']:.3f}, diff {s['mean_diff']:+.2f}"))
    else:
        checks.append(("Pairwise quality", False, f"not run for every roll: `eval/pairwise.py --a <roll> --b <reference>`"))

    ok = all(c[1] for c in checks)
    lines = [f"## Release gate\n\nAgainst `{', '.join(ref_ids)}`: **{'PASS' if ok else 'FAIL'}**\n",
             "| Check | Result | Detail |", "|---|---|---|",
             *[f"| {n} | {'pass' if p else '**fail**'} | {d} |" for n, p, d in checks]]
    changes = sum((changed(*x) for x in zip(rolls, refs, pws)), [])
    if changes:
        lines += ["", "What changed against the reference:", "", "| Query | Change | Detail |", "|---|---|---|", *changes]
    return ok, "\n".join(lines) + "\n"


def per_question(pws):
    """One pairwise row per question, the mean of its rolls. Pooled rolls count each question twice, which makes the
    sign test's p too small; a how-to question repeats the same two answers if the edit leaves its prompt alone."""
    by_id = {}
    for p in sum(pws, []):
        by_id.setdefault(p["id"], []).append(p)
    out = []
    for qid, ps in by_id.items():
        judged = [p for p in ps if not p.get("identical")]
        diff = statistics.fmean(p["diff"] for p in ps)
        out.append({"id": qid, "diff": diff, "outcome": "win" if diff > 0 else "loss" if diff < 0 else "tie",
                    "identical": not judged, "orders": [],
                    "a": statistics.fmean(p["a"] for p in judged) if judged else None,
                    "b": statistics.fmean(p["b"] for p in judged) if judged else None})
    return out


def changed(rows, ref_rows, pw):
    """Per-query flips of the deterministic checks and the abstention judge, and pairwise losses."""
    ref = {r["id"]: r for r in ref_rows}
    out = []
    for r in rows:
        b = ref.get(r["id"])
        if not b:
            continue
        if r.get("expect_ok") is not None and r["expect_ok"] != b.get("expect_ok"):
            out.append(f"| `{r['id']}` | expected answer {'now found' if r['expect_ok'] else '**lost**'} "
                       f"| {cell(r.get('answer'), 140)} |")
        a1, a0 = r.get("abstain"), b.get("abstain")
        if a1 and a0 and a1["pass"] != a0["pass"]:
            out.append(f"| `{r['id']}` | abstention {'now passes' if a1['pass'] else '**fails**'} | {cell(a1['reason'], 140)} |")
    for p in pw:
        if p["outcome"] == "loss" and p["orders"]:
            o = p["orders"][0]
            out.append(f"| `{p['id']}` | pairwise **loss** ({p['a']:.1f} vs {p['b']:.1f}) "
                       f"| candidate = {o.get('a_pos', '?')}: {cell(o.get('rationale'), 160)} |")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", help="run id under eval/runs/ (default: newest)")
    ap.add_argument("--out", default=str(REPO / "docs/EVAL.md"))
    ap.add_argument("--history", default=str(EVAL / "history.csv"), help="the CSV that gets this run's row")
    ap.add_argument("--systems", metavar="RUN,RUN", help="add a table of this run and these runs on their shared queries")
    ap.add_argument("--roll2", metavar="RUN", help="the candidate's second roll, for the gate")
    ap.add_argument("--gate", metavar="REF_RUN[,REF_RUN]", help="add the release gate against the reference runs, one per roll; exit 1 on fail")
    a = ap.parse_args()

    run_dir = RUNS / a.run if a.run else max(RUNS.iterdir(), key=lambda p: p.stat().st_mtime)
    meta, qmeta, rows = load_run(run_dir)

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
                "recall@ev": (r["recall"] or {}).get("recall@ev"),
                "recall_wiki@10": (r["recall"] or {}).get("recall_wiki@10"),
                "evidence": (r["evidence_label"] or {}).get("sufficiency"),
                "card_ms": rec.get("card_ms"), "ttft_ms": rec.get("ttft_ms"),
                "total_ms": rec.get("total_ms"), "decode_tok_s": decode_tps(r),
                **citation_stats(r), "expect_ok": r["expect_ok"],
                "abstain_pass": (r["abstain"] or {}).get("pass"),
            }, ensure_ascii=False) + "\n")

    cats = [c for c in CATEGORIES if any(r.get("category") == c for r in rows)]
    cats += sorted({r.get("category") or "?" for r in rows} - set(cats))
    by_cat = {c: summarize([r for r in rows if (r.get("category") or "?") == c]) for c in cats}
    overall = summarize(rows)
    groups = [*by_cat.items(), ("**overall**", overall)]
    smoke = any(qmeta.get(r["id"], {}).get("smoke") for r in rows)

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    report = render(meta, rows, overall, groups, by_cat, smoke, findings(rows, qmeta, overall, by_cat))
    if a.systems:
        report = report.replace("## Reproduce\n", systems([meta["run_id"], *a.systems.split(",")]) + "\n## Reproduce\n", 1)
    gate_ok = True
    if a.gate:
        run_dirs = [run_dir, *([RUNS / a.roll2] if a.roll2 else [])]
        if len(a.gate.split(",")) != len(run_dirs):
            sys.exit("--gate needs one reference run per roll")
        gate_ok, section = gate(run_dirs, a.gate.split(","))
        report = report.replace("## Results\n", section + "\n## Results\n", 1)
    Path(a.out).write_text(report)

    hist = Path(a.history)
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
    if a.gate:
        print(f"release gate against {a.gate}: {'PASS' if gate_ok else 'FAIL'}")
        sys.exit(0 if gate_ok else 1)


if __name__ == "__main__":
    main()
