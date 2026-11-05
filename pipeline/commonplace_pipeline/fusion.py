"""Train learned fusion: a small scorer that orders search candidates before the cut to the rerank pool.

The target: the 24-passage pool must hold the passages that the reranker would rank in its top 8 if it saw
every candidate. The scorer sees only numbers (ranks and scores in the keyword and semantic lists, entity
links, pack statistics, popularity, passage position and length), never passage text.

  questions  training questions from NQ-open (train) and Dolly-15k (open_qa, general_qa), without eval
             questions or near copies of them. No Stack Exchange text: its dumps forbid LLM training.
  pools      `commonplace pool --candidates` for each question, in shards: every candidate with its
             features and the app reranker's score
  teacher    a larger cross-encoder's score for the app reranker's best 48 candidates (labels only; the
             scorer never sees text)
  train      fit the weights on a question split, report pool recall on the held-out split, and with
             --write write core/commonplace-core/src/fusion_model.json
  labeled    nDCG@8 after the rerank on the 161-question labeled set, RRF against learned; Claude Sonnet
             grades passages that the old labels do not cover
  nq         NQ-open validation hit@k after the rerank, RRF against learned

  W=data/work/fusion
  python -m commonplace_pipeline.fusion questions --out $W/questions.jsonl
  python -m commonplace_pipeline.fusion pools --questions $W/questions.jsonl --library data/library-se2 --out $W/pools
  python -m commonplace_pipeline.fusion teacher --pools $W/pools --out $W/teacher.jsonl
  python -m commonplace_pipeline.fusion train --pools $W/pools --teacher $W/teacher.jsonl --write --report $W/train-r17-mlp16.json
  python -m commonplace_pipeline.fusion labeled --out $W/labeled          # needs the backup drive
  python -m commonplace_pipeline.fusion nq --out $W/nq

Run inside `nix develop` (CUDA for `teacher`) with PYTHONPATH=pipeline.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import subprocess
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "core/target/release/commonplace"
MODEL_OUT = ROOT / "core/commonplace-core/src/fusion_model.json"
PHONE = ["--fuse-keep=24", "--leaf-model=onnx/model_quantized.onnx", "--ettin-model=onnx/model_sel8.onnx"]
TEACHER = "cross-encoder/ettin-reranker-400m-v1"
POOL = 24  # the phone's rerank pool (`--fuse-keep=24`)
TOP = 8  # the evidence the reranker keeps (`rerank_keep`)
PER_ARTICLE = 4
EVIDENCE_PER_ARTICLE = 3
KEYWORD_ONLY_KEEP = 5
LOADED_FIRST = "pop"  # the first of `fusion::LOADED`; the features before it are `fusion::CHEAP`


def read_jsonl(path) -> list[dict]:
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def same(text: str) -> str:
    return " ".join("".join(c for c in text.lower() if c.isalnum() or c == " ").split())


# ---------------------------------------------------------------- questions


def cmd_questions(a) -> None:
    import pyarrow.parquet as pq

    rng = random.Random(a.seed)
    held = set()
    for f in glob.glob(str(ROOT / "eval/*.jsonl")):
        for r in read_jsonl(f):
            for q in [r.get("query", "")] + [t.get("query", "") for t in r.get("history", [])]:
                if q:
                    held.add(same(q))
    held_words = [set(h.split()) for h in held]

    def near_eval(q: str) -> bool:
        w = set(same(q).split())
        return same(q) in held or any(len(w & h) / max(1, len(w | h)) >= 0.6 for h in held_words)

    nq = pq.read_table(Path(a.src) / "train-00000-of-00001.parquet").column("question").to_pylist()
    dolly = [r["instruction"].strip() for r in read_jsonl(Path(a.src) / "databricks-dolly-15k.jsonl")
             if r["category"] in ("open_qa", "general_qa") and not r["context"].strip()
             and r["instruction"].strip().endswith("?") and len(r["instruction"].split()) <= 30]
    out, seen = [], set()
    for name, qs, n in (("nq", nq, a.nq), ("dolly", dolly, a.dolly)):
        rng.shuffle(qs)
        k = 0
        for q in qs:
            if k >= n:
                break
            if same(q) in seen or near_eval(q):
                continue
            seen.add(same(q))
            out.append({"id": f"{name}-{k:05d}", "query": q, "source": name})
            k += 1
    rng.shuffle(out)
    with open(a.out, "w") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(out)} questions -> {a.out}")


# ---------------------------------------------------------------- pools


def cmd_pools(a) -> None:
    qs = read_jsonl(a.questions)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    procs = []
    for i in range(a.shards):
        path = out / f"shard{i}.jsonl"
        done = {json.loads(l).get("id") for l in path.open()} if path.exists() else set()
        todo = [q for j, q in enumerate(qs) if j % a.shards == i and q["id"] not in done]
        if not todo:
            continue
        inp = out / f"shard{i}.in"
        inp.write_text("".join(json.dumps({"id": q["id"], "query": q["query"]}) + "\n" for q in todo))
        f = path.open("a")
        p = subprocess.Popen([str(a.bin), "--library", a.library, *PHONE, "--threads", str(a.threads), "pool",
                              "--candidates", str(a.depth)], stdin=inp.open(), stdout=f, stderr=subprocess.DEVNULL,
                             text=True, cwd=ROOT, env={**os.environ, "RAYON_NUM_THREADS": str(a.threads)})
        procs.append((p, f, len(todo)))
    t0 = time.time()
    for p, f, n in procs:
        p.wait()
        f.close()
        print(f"shard done: {n} questions, rc {p.returncode}, {time.time() - t0:.0f} s", flush=True)


def load_pools(pattern: str):
    """Pools of every shard; the header line of each shard names the features. A shard that is still being
    written can end in a partial line."""
    names, pools = None, []
    for path in sorted(glob.glob(str(Path(pattern) / "shard*.jsonl"))):
        for line in open(path):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "feature_names" in r:
                names = r["feature_names"]
            else:
                pools.append(r)
    return names, pools


# ---------------------------------------------------------------- teacher


def cmd_teacher(a) -> None:
    import torch
    from sentence_transformers import CrossEncoder

    out = Path(a.out)
    done = {json.loads(l)["id"] for l in out.open()} if out.exists() else set()
    _, pools = load_pools(a.pools)
    pools = [p for p in pools if p["id"] not in done]
    ce = CrossEncoder(a.model, max_length=256, activation_fn=torch.nn.Identity(), model_kwargs={"dtype": torch.float16})
    t0, n = time.time(), 0
    with out.open("a") as f:
        for i, p in enumerate(pools):
            # The teacher runs at about 135 pairs/s on the shared GPU, so it scores only the app reranker's best
            # `--top`; the rest count as irrelevant.
            idx = sorted(range(len(p["hits"])), key=lambda j: -p["hits"][j]["rerank"])[: a.top]
            pairs = [(p["query"], p["hits"][j]["text"]) for j in idx]
            s = ce.predict(pairs, batch_size=a.batch, show_progress_bar=False).tolist() if pairs else []
            f.write(json.dumps({"id": p["id"], "idx": idx, "scores": s}) + "\n")
            n += len(pairs)
            if i % 50 == 0:
                f.flush()
                print(f"{i + 1}/{len(pools)} questions, {n / (time.time() - t0):.0f} pairs/s", flush=True)


# ---------------------------------------------------------------- train


def rerank_penalty(title: str, pack: str) -> float:
    """`list_penalty` and `pack_penalty` of retrieval.rs, which the app subtracts from the reranker's score."""
    t = title.lower()
    year = t.split(" in ", 1)[0]
    listy = t.startswith(("list of ", "lists of ", "timeline of ", "glossary of ", "outline of ", "index of ")) or (
        " in " in t and len(year) == 4 and year.isdigit())
    return 0.5 * listy + 0.5 * pack.startswith("wikiquote")


def teacher_scores(r: dict, n: int) -> np.ndarray:
    """Scores for every candidate; one the teacher skipped ranks below all it scored."""
    idx = r.get("idx", range(n))
    t = np.full(n, min(r["scores"], default=0.0) - 100.0, dtype=np.float32)
    t[list(idx)] = r["scores"]
    return t


class Pools:
    """Numeric arrays per question: features, reranker scores, articles. No passage text; a title only sets
    the reranker's list penalty in the target."""

    def __init__(self, names: list[str], pools: list[dict], teacher: dict[str, list[float]]):
        self.names = names
        self.q = []
        for p in pools:
            if not p["hits"]:
                continue
            hs = p["hits"]
            self.q.append({
                "id": p["id"],
                "x": np.array([h["feat"] for h in hs], dtype=np.float32),
                "r17": np.array([h["rerank"] - rerank_penalty(h["title"], h["pack"]) for h in hs], dtype=np.float32),
                "t400": teacher_scores(teacher[p["id"]], len(hs)) if p["id"] in teacher else None,
                "article": [(h["pack"], h["article"]) for h in hs],
            })


def simulate(q: dict, pre: np.ndarray, post: np.ndarray, entities: bool, pool: int = POOL) -> list[int]:
    """The pool that `Retriever::fused` keeps, as candidate indices: the best 3 × pool by `pre`, then by
    `pre + post` with at most four passages per article. A keyword-only pack's best hits always stay, and
    with `entities` (learned fusion) the linked entities' leads."""
    x = q["x"]
    kw = x[:, NAMES.index("kw_only")] > 0
    pack_rank = np.exp(x[:, NAMES.index("s_plr")]) - 1
    best_kw = kw & (x[:, NAMES.index("s_in")] > 0) & (pack_rank < KEYWORD_ONLY_KEEP - 0.5)
    if entities:
        best_kw |= x[:, NAMES.index("e_in")] > 0
    first = list(np.argsort(-pre, kind="stable")[:pool * 3])
    first += [i for i in np.flatnonzero(best_kw) if i not in first]
    first.sort(key=lambda i: -(pre[i] + post[i]))
    seen: dict = {}
    out = []
    for i in first:
        a = q["article"][i]
        seen[a] = seen.get(a, 0) + 1
        if seen[a] > PER_ARTICLE:
            continue
        if len(out) < pool or best_kw[i]:
            out.append(i)
    return out


NAMES: list[str] = []


def target(q: dict, scores: str) -> list[int]:
    """The evidence the app would pick if the reranker saw every candidate: the best 8 by `scores`, at most
    three per article (`select_diverse`)."""
    seen: dict = {}
    out = []
    for i in np.argsort(-q[scores], kind="stable"):
        a = q["article"][i]
        if seen.get(a, 0) < EVIDENCE_PER_ARTICLE:
            seen[a] = seen.get(a, 0) + 1
            out.append(int(i))
            if len(out) == TOP:
                break
    return out


N_CHEAP = 0
RRF = {"w": {"rrf": 0.01, "pop": 0.005}}


def scores(model: dict, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(score before the load, score of the loaded features), as `fusion::Model` computes them."""
    w = np.array([model["w"].get(n, 0.0) for n in NAMES], dtype=np.float32)
    pre = x[:, :N_CHEAP] @ w[:N_CHEAP]
    if m := model.get("mlp"):
        h = np.tanh(x[:, [NAMES.index(n) for n in m["inputs"]]] @ np.array(m["w1"], dtype=np.float32).T + np.array(m["b1"], dtype=np.float32))
        pre = pre + h @ np.array(m["w2"], dtype=np.float32)
    return pre, x[:, N_CHEAP:] @ w[N_CHEAP:]


def evaluate(qs: list[dict], model: dict, teacher: str) -> dict:
    rec = []
    qs = [q for q in qs if q[teacher] is not None]
    for q in qs:
        pool = set(simulate(q, *scores(model, q["x"]), entities=model is not RRF))
        rec.append(np.mean([i in pool for i in target(q, teacher)]))
    return {"recall": float(np.mean(rec)), "n": len(qs)}


def fit(qs: list[dict], teacher: str, l2: float, epochs: int, temp: float, hidden: int, seed: int) -> dict:
    """Listwise: softmax of the score over each question's candidates against the teacher's top 8, each
    weighted by the teacher's softmax at `temp`. The score is linear in every feature, plus with `hidden` a
    tanh layer over the features known before the load. Features are standardised for the fit and folded back."""
    import torch

    torch.manual_seed(seed)
    qs = [q for q in qs if q[teacher] is not None]
    allx = np.concatenate([q["x"] for q in qs])
    mu, sd = allx.mean(0), allx.std(0) + 1e-6
    data = []
    for q in qs:
        t = q[teacher]
        y = np.zeros(len(t), dtype=np.float32)
        top = np.array(target(q, teacher))
        y[top] = np.exp((t[top] - t[top].max()) / temp)
        y /= y.sum()
        data.append((torch.tensor((q["x"] - mu) / sd), torch.tensor(y)))
    w = torch.zeros(allx.shape[1], requires_grad=True)
    params = [w]
    if hidden:
        l1 = torch.nn.Linear(N_CHEAP, hidden)
        w2 = torch.nn.Parameter(torch.randn(hidden) * 0.1)
        params += [*l1.parameters(), w2]
    score = lambda x: x @ w + (torch.tanh(l1(x[:, :N_CHEAP])) @ w2 if hidden else 0)
    opt = torch.optim.Adam(params, lr=0.05 if not hidden else 0.01)
    for ep in range(epochs):
        random.Random(seed + ep).shuffle(data)
        for i in range(0, len(data), 64):
            opt.zero_grad()
            loss = sum(-(y * torch.log_softmax(score(x), 0)).sum() for x, y in data[i:i + 64]) / len(data[i:i + 64])
            loss = loss + l2 * sum((p ** 2).sum() for p in params)
            loss.backward()
            opt.step()
    # The bias and the mean shift do not change an order; drop them.
    wn = w.detach().numpy() / sd
    model = {"w": {n: float(v) for n, v in zip(NAMES, wn) if abs(v) > 0}}
    if hidden:
        w1 = l1.weight.detach().numpy() / sd[:N_CHEAP]
        b1 = l1.bias.detach().numpy() - w1 @ mu[:N_CHEAP]
        model["mlp"] = {"inputs": NAMES[:N_CHEAP], "w1": w1.tolist(), "b1": b1.tolist(), "w2": w2.detach().numpy().tolist()}
    return model


def cmd_train(a) -> None:
    global NAMES, N_CHEAP
    names, pools = load_pools(a.pools)
    teacher = {r["id"]: r for r in read_jsonl(a.teacher)} if a.teacher else {}
    data = Pools(names, pools, teacher)
    NAMES = names
    N_CHEAP = names.index(LOADED_FIRST)
    qs = sorted(data.q, key=lambda q: (int(q["id"].split("-")[1]), q["id"]))
    rng = random.Random(a.seed)
    rng.shuffle(qs)
    k = int(len(qs) * a.holdout)
    test, train = qs[:k], qs[k:]
    drop = set(a.drop.split(",")) if a.drop else set()
    if drop:
        for q in qs:
            q["x"] = q["x"].copy()
            for n in drop:
                q["x"][:, NAMES.index(n)] = 0
    print(f"{len(train)} train, {len(test)} held-out questions; target: {a.target} top {TOP} in a pool of {POOL}")
    model = fit(train, a.target, a.l2, a.epochs, a.temp, a.hidden, a.seed)
    report = {"target": a.target, "train": len(train), "holdout": len(test), "drop": sorted(drop), "hidden": a.hidden,
              "temp": a.temp, "l2": a.l2, "model": model, "results": {}}
    for label, ww in (("rrf", RRF), ("learned", model)):
        for t in ("r17", "t400"):
            for src in ("all", "nq", "dolly"):
                sub = [q for q in test if src == "all" or q["id"].startswith(src)]
                r = evaluate(sub, ww, t)
                report["results"][f"{label}/{t}/{src}"] = r
                print(f"{label:8s} recall@{POOL} of {t} top {TOP} ({src:5s}, n={r['n']}): {r['recall']:.3f}")
    if a.report:
        Path(a.report).write_text(json.dumps(report, indent=1) + "\n")
    if a.write:
        MODEL_OUT.write_text(json.dumps(model) + "\n")
        print(f"wrote {MODEL_OUT}")


# ---------------------------------------------------------------- labeled


GRADE_SYSTEM = """You grade search results for a question-answering app. Today is {today}.
You see a question and numbered passages in random order. Judge each passage alone, from its text, by how much it helps answer the question.
- grade 2: the passage itself contains the answer, or a main part of it (the facts, the mechanism, the steps).
- grade 1: the passage helps but does not answer: related background, a partial fact, an answer to a neighbouring question.
- grade 0: the passage does not help.
- restates: true when the passage mostly repeats or discusses the question (for example a forum question, an unanswered thread, or comments about the question) without giving a usable answer. Set grade 0 for such a passage. Otherwise false.
Grade the content, not the source. Do not prefer a source type. Return one entry for every passage."""
GRADE_SCHEMA = {"type": "object", "properties": {"labels": {"type": "array", "items": {"type": "object",
    "properties": {"n": {"type": "integer"}, "grade": {"type": "integer", "enum": [0, 1, 2]}, "restates": {"type": "boolean"}},
    "required": ["n", "grade", "restates"]}}}, "required": ["labels"]}


def grade_with_claude(query: str, texts: list[str], seed: str) -> tuple[list[int], float]:
    """The prompt of the 161-question labeled set (rerank/2026-10-11/teacher/label.py), Claude Sonnet."""
    import sys
    from datetime import date

    sys.path.insert(0, str(ROOT / "eval"))
    from common import claude_json

    order = list(range(len(texts)))
    random.Random(seed).shuffle(order)
    body = "\n\n".join(f"[{k + 1}]\n{texts[i]}" for k, i in enumerate(order))
    out, raw = claude_json(f"Question: {query}\n\n=== Passages ===\n{body}", GRADE_SYSTEM.format(today=date.today().isoformat()),
                           GRADE_SCHEMA, "sonnet", "", 0.4)
    got = {l["n"]: l for l in out["labels"]}
    grades = [0] * len(texts)
    for k, i in enumerate(order):
        l = got[k + 1]
        grades[i] = 0 if l["restates"] else l["grade"]
    return grades, float(raw.get("total_cost_usd") or 0.0)


def dcg(gs) -> float:
    return sum((2 ** g - 1) / np.log2(r + 2) for r, g in enumerate(gs))


def cmd_labeled(a) -> None:
    """nDCG@8 after the rerank on the 161-question labeled set, for each fusion. A passage is matched to its
    old label by its reranker text; passages without a label are graded with the same prompt, within a budget."""
    import hashlib

    src = Path(a.labels)
    old_pools = {p["id"]: p for p in read_jsonl(src / "pools.jsonl")}
    old_labels = {r["id"]: r["labels"] for r in read_jsonl(src / "labels.jsonl")}
    qtypes = json.loads((src / "qtypes.json").read_text())
    queries = [q for q in read_jsonl(src / "queries.jsonl") if q["id"] in old_labels]
    key = lambda qid, text: hashlib.sha1(f"{qid}\0{text}".encode()).hexdigest()
    grade: dict[str, int] = {}
    for qid, p in old_pools.items():
        for h, l in zip(p["hits"], old_labels.get(qid, [])):
            grade[key(qid, h["text"])] = 0 if l["restates"] else l["grade"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    new_path = out / "labels-new.jsonl"
    for r in read_jsonl(new_path) if new_path.exists() else []:
        grade[r["key"]] = r["grade"]
    tops: dict[str, dict] = {}
    for fusion in a.fusion.split(","):
        pools = run_pools(a, queries, fusion, out / f"pools-{Path(a.library).name}-{fusion}.jsonl")
        for qid, p in pools.items():
            hs = sorted(p["hits"], key=lambda h: -(h["rerank"] - rerank_penalty(h["title"], h["pack"])))[:TOP]
            tops.setdefault(qid, {})[fusion] = hs
    from concurrent.futures import ThreadPoolExecutor

    jobs = []
    for q in queries:
        todo = list({h["text"]: h for hs in tops.get(q["id"], {}).values() for h in hs if key(q["id"], h["text"]) not in grade})
        if todo:
            jobs.append((q, todo))
    print(f"{len(jobs)} questions with {sum(len(t) for _, t in jobs)} passages to grade")
    spent = 0.0
    with new_path.open("a") as f, ThreadPoolExecutor(4) as ex:
        for i in range(0, len(jobs), 4):
            if spent >= a.budget:
                print(f"budget reached: {len(jobs) - i} questions keep unlabeled passages")
                break
            batch = jobs[i : i + 4]
            for (q, todo), (gs, cost) in zip(batch, ex.map(lambda j: grade_with_claude(j[0]["query"], j[1], j[0]["id"]), batch)):
                spent += cost
                for t, g in zip(todo, gs):
                    grade[key(q["id"], t)] = g
                    f.write(json.dumps({"key": key(q["id"], t), "id": q["id"], "grade": g}) + "\n")
            f.flush()
            print(f"{i + len(batch)}/{len(jobs)} questions graded, ${spent:.2f}", flush=True)
    rows = []
    for q in queries:
        known_texts = {h["text"] for h in old_pools[q["id"]]["hits"]} | {h["text"] for hs in tops[q["id"]].values() for h in hs}
        ideal = dcg(sorted((grade.get(key(q["id"], t), 0) for t in known_texts), reverse=True)[:TOP])
        for fusion, hs in tops[q["id"]].items():
            gs = [grade.get(key(q["id"], h["text"])) for h in hs]
            g0 = [g or 0 for g in gs]
            rows.append({"id": q["id"], "qtype": qtypes.get(q["id"]), "fusion": fusion, "ndcg": dcg(g0) / ideal if ideal else None,
                         "p8": sum(g >= 1 for g in g0) / TOP, "unlabeled": sum(g is None for g in gs),
                         "top": [f"{h['pack']}: {h['title']} [{g}]" for h, g in zip(hs, gs)]})
    with (out / f"labeled-{Path(a.library).name}.jsonl").open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for qt in (None, "explain", "lookup", "compare"):
        for fusion in a.fusion.split(","):
            sub = [r for r in rows if r["fusion"] == fusion and (qt is None or r["qtype"] == qt) and r["ndcg"] is not None]
            print(f"{fusion:8s} {qt or 'all':8s} n={len(sub):3d} nDCG@8 {np.mean([r['ndcg'] for r in sub]):.3f} "
                  f"P@8 {np.mean([r['p8'] for r in sub]):.3f} unlabeled {sum(r['unlabeled'] for r in sub)}")


# ---------------------------------------------------------------- nq


def norm_answer(s: str) -> str:
    import re
    import unicodedata

    s = unicodedata.normalize("NFKD", s).lower()
    return " " + " ".join(re.sub(r"[^\w\s]", " ", s).split()) + " "


def run_pools(a, queries: list[dict], fusion: str, cache: Path) -> dict[str, dict]:
    """`commonplace pool` (the 24-passage rerank pool) for each query, cached."""
    if not cache.exists():
        inp = "".join(json.dumps({"id": q["id"], "query": q["query"]}) + "\n" for q in queries)
        r = subprocess.run([str(a.bin), "--library", a.library, *PHONE, "--threads", "7", "--fusion", fusion, "pool"], input=inp,
                           capture_output=True, text=True, cwd=ROOT, check=True, env={**os.environ, "RAYON_NUM_THREADS": "7"})
        cache.write_text(r.stdout)
    return {str(p["id"]): p for p in read_jsonl(cache)}


def cmd_nq(a) -> None:
    """NQ-open validation (held out: training used the train split): a question is a hit at k when an answer
    string occurs in one of the reranker's best k passages of the pool."""
    qs = read_jsonl(a.answers)[: a.n]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ks = (1, 3, 5, 8, 24)
    for fusion in a.fusion.split(","):
        pools = run_pools(a, qs, fusion, out / f"nq{a.n}-{Path(a.library).name}-{fusion}.jsonl")
        hits = np.zeros(len(ks))
        for q in qs:
            p = pools[str(q["id"])]
            hs = sorted(p["hits"], key=lambda h: -(h["rerank"] - rerank_penalty(h["title"], h["pack"])))
            ans = [norm_answer(x) for x in q["answers"]]
            ok = [any(x in norm_answer(h["text"]) for x in ans) for h in hs]
            hits += [any(ok[:k]) for k in ks]
        print(f"{fusion:8s} n={len(qs)} " + " ".join(f"hit@{k} {h / len(qs):.3f}" for k, h in zip(ks, hits)))


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("questions")
    q.add_argument("--src", default=str(ROOT / "data/work/fusion/src"), help="nq_open train parquet and dolly jsonl")
    q.add_argument("--out", required=True)
    q.add_argument("--nq", type=int, default=2000)
    q.add_argument("--dolly", type=int, default=2000)
    q.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("pools")
    p.add_argument("--questions", required=True)
    p.add_argument("--library", default="data/library-se2")
    p.add_argument("--out", required=True)
    p.add_argument("--bin", default=str(BIN))
    p.add_argument("--depth", type=int, default=150)
    p.add_argument("--shards", type=int, default=2)
    p.add_argument("--threads", type=int, default=4)
    t = sub.add_parser("teacher")
    t.add_argument("--pools", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--model", default=TEACHER)
    t.add_argument("--batch", type=int, default=64)
    t.add_argument("--top", type=int, default=48)
    r = sub.add_parser("train")
    r.add_argument("--pools", required=True)
    r.add_argument("--teacher", help="teacher scores (`teacher`); without them only the app reranker is a target")
    r.add_argument("--target", default="r17", choices=["r17", "t400"], help="the app reranker or the teacher")
    r.add_argument("--holdout", type=float, default=0.2)
    r.add_argument("--l2", type=float, default=0.0)
    r.add_argument("--epochs", type=int, default=30)
    r.add_argument("--temp", type=float, default=100.0, help="100: the top 8 weigh about the same")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--hidden", type=int, default=16, help="tanh units over the features known before the load (0: linear)")
    r.add_argument("--drop", help="features to leave out, comma-separated")
    r.add_argument("--report")
    r.add_argument("--write", action="store_true", help="write core/commonplace-core/src/fusion_model.json")
    l = sub.add_parser("labeled")
    l.add_argument("--labels", default="/mnt/backup/llm-wiki/work/rerank/2026-10-11/teacher", help="pools, labels, queries, qtypes")
    l.add_argument("--library", default="data/library-se2")
    l.add_argument("--bin", default=str(BIN))
    l.add_argument("--fusion", default="rrf,learned")
    l.add_argument("--out", required=True)
    l.add_argument("--budget", type=float, default=4.0, help="USD for new labels")
    n = sub.add_parser("nq")
    n.add_argument("--answers", default="/mnt/backup/llm-wiki/work/eval-kit/answers2610.jsonl")
    n.add_argument("--n", type=int, default=600)
    n.add_argument("--library", default="data/library-se2")
    n.add_argument("--bin", default=str(BIN))
    n.add_argument("--fusion", default="rrf,learned")
    n.add_argument("--out", required=True)
    a = ap.parse_args()
    {"questions": cmd_questions, "pools": cmd_pools, "teacher": cmd_teacher, "train": cmd_train, "labeled": cmd_labeled,
     "nq": cmd_nq}[a.cmd](a)


if __name__ == "__main__":
    main()
