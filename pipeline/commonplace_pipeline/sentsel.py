"""Train the sentence head: which sentences of a reranked passage the answer needs.

The head reads the reranker's own token states (Ettin-17m, no extra encoder pass): the mean of the states of
each sentence piece (`evidence::pieces` in the core), optionally with the CLS state through a small MLP. The
held-out AUC picks the size; one linear layer on the mean won (2026-11-03). Silver labels come from Claude, as
in Provence (Chirkova et al., 2025): it grades every piece of a question's top passages.
Files live in data/work/sentsel/ (--dir).

  questions  Claude writes search questions per topic; repeats of eval questions are dropped
  pool       `commonplace pool` with the phone settings, for every question
  label      Claude grades each piece of a question's top passages: needed, helpful or not
  train      reranker states per piece (fp32 and int8 encoders), the head, held-out metrics against word
             overlap; writes core/commonplace-core/src/sentence_head.json
  compare    evidence-judge labels of eval runs against a base run: full rate, mean grade, paired wins

  P=pipeline/.venv/bin/python; S="nix develop -c env PYTHONPATH=$PWD/pipeline $P -m commonplace_pipeline.sentsel"
  $S questions --per-topic 30
  $S pool
  $S label --limit 500 --jobs 6
  $S train --hidden 0 16 64
  $S compare --base x-sentsel-overlap-600 --runs x-sentsel-head-600 x-sentsel-head-500 x-sentsel-head-400 x-sentsel-head-300

Result (2026-11-03, docs/BENCHMARKS.md): the core uses 500 evidence tokens (400 on a warm phone) on CPU models.
At 400, how-to questions lost steps out of their lists; filling the gaps between chosen sentences did not fix it.
`train` caches the reranker states in feats-model.npz and feats-model_sel8.npz; it remakes missing ones.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "core/target/release/commonplace"
ETTIN = ROOT / "data/models/ettin-17m"
MODELS = ["onnx/model.onnx", "onnx/model_sel8.onnx"]  # desktop, phone
PHONE_CLI = ["--fuse-keep=24", "--leaf-model=onnx/model_quantized.onnx", "--ettin-model=onnx/model_sel8.onnx"]
MAX_LEN = 256  # RERANK_MAX_TOKENS in commonplace-core
TOP = 8  # rerank_keep: the passages that can enter the evidence
EVAL_FILES = ["eval/queries.jsonl", "eval/practical.jsonl", "eval/recent.jsonl", "eval/canary.jsonl",
              "eval/abstention.jsonl", "eval/robust.jsonl", "eval/conversation-test.jsonl", "scripts/bench/perf.jsonl"]

sys.path.insert(0, str(ROOT / "eval"))
from common import claude_json, read_jsonl  # noqa: E402

TOPICS = [
    "world history before 1900", "20th-century history and wars", "politics, governments and law",
    "countries, cities and geography", "physics", "chemistry", "biology and the human body", "astronomy and space",
    "diseases, symptoms and treatments", "first aid and emergency care", "travel health and vaccines",
    "travel destinations and sights", "cooking and food", "household repairs and practical how-to",
    "Linux and system administration", "programming languages and APIs", "mathematics and statistics",
    "economics and business", "literature and authors", "music and musicians", "film and television",
    "sports and athletes", "inventions and the history of technology", "biographies of famous people",
    "animals and plants", "word meanings and etymology", "numbers: sizes, populations, distances, dates",
    "comparisons of two things (countries, animals, planets, products)", "religion and philosophy",
    "art, architecture and famous buildings",
]

Q_SYSTEM = """You write realistic questions that people type into an offline research app (a phone app that
searches Wikipedia, Wikivoyage, textbooks, medical references, Arch Wiki and programming docs).
Mix the forms: short fact lookups, "why" and "how does" explanations, comparisons of two things, questions
that ask for a number, "how do I" tasks, and some casual or vague wording. One question per item, at most
25 words, no numbering. Do not repeat a question."""
Q_SCHEMA = {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
            "required": ["questions"]}

L_SYSTEM = """You label training data for a system that shows a small language model only the most useful
sentences of retrieved passages. You see a question and numbered passages; each sentence (or piece of a long
sentence) has an id such as 3.2 (passage 3, sentence 2).
Grade each sentence by what a good answer to the question needs:
- needed: it states a fact the answer uses, or the answer itself.
- helpful: it is not the answer but makes a needed sentence clear or correct (names the subject a pronoun
  refers to, gives the date, place or condition the fact holds under).
Every other sentence is not useful. Judge each sentence on its own merit, also in passages that do not answer
the question. Most sentences are not useful; list only the needed and helpful ids."""
L_SCHEMA = {"type": "object", "properties": {"needed": {"type": "array", "items": {"type": "string"}},
                                             "helpful": {"type": "array", "items": {"type": "string"}}},
            "required": ["needed", "helpful"]}


def same(text: str) -> str:
    return " ".join("".join(c for c in text.lower() if c.isalnum() or c == " ").split())


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


# ---------------------------------------------------------------- questions


def cmd_questions(a) -> None:
    out = a.dir / "questions.jsonl"
    rows = read_jsonl(out)
    done = {r["topic"] for r in rows}
    evals = {same(q["query"]) for f in EVAL_FILES for q in read_jsonl(ROOT / f)}
    seen = {same(r["query"]) for r in rows}
    cost = 0.0

    def ask(topic: str):
        return claude_json(f"Write {a.per_topic} different questions about {topic}.", Q_SYSTEM, Q_SCHEMA, a.model, "", 0.5)

    todo = [t for t in TOPICS if t not in done]
    with ThreadPoolExecutor(a.jobs) as pool:
        for topic, (res, raw) in zip(todo, pool.map(ask, todo)):
            cost += raw.get("total_cost_usd") or 0
            for q in res["questions"]:
                k = same(q)
                if k and k not in seen and k not in evals:
                    seen.add(k)
                    rows.append({"id": f"ss-{len(rows):04d}", "topic": topic, "query": q.strip()})
    write_jsonl(out, rows)
    print(f"{len(rows)} questions in {out}, ${cost:.2f}")


# ---------------------------------------------------------------- pool


def cmd_pool(a) -> None:
    qs = read_jsonl(a.dir / "questions.jsonl")
    out = a.dir / "pools.jsonl"
    done = {r["id"] for r in read_jsonl(out)}
    todo = [q for q in qs if q["id"] not in done]
    with open(out, "a") as f:
        subprocess.run([str(BIN), "--library", a.library, *PHONE_CLI, "pool"], input="".join(json.dumps(q) + "\n" for q in todo),
                       stdout=f, text=True, check=True, cwd=ROOT)
    print(f"{len(todo)} pools added to {out}")


def top_hits(pool: dict) -> list[dict]:
    """The best `TOP` hits by rerank score, at most three per article, as the engine's `select_diverse`."""
    hits = sorted((h for h in pool["hits"] if not h["pack"].startswith("stackexchange")), key=lambda h: -(h["rerank"] or -1e9))
    out, per = [], {}
    for h in hits:
        k = (h["pack"], h["title"])
        if per.get(k, 0) < 3:
            per[k] = per.get(k, 0) + 1
            out.append(h)
        if len(out) == TOP:
            break
    return out


def char_spans(text: str, spans: list[list[int]]) -> list[tuple[int, int]]:
    """Byte spans from the core into Python string indices."""
    b = text.encode()
    return [(len(b[:x].decode()), len(b[:y].decode())) for x, y in spans]


def pieces_of(h: dict) -> list[str]:
    return [h["text"][x:y] for x, y in char_spans(h["text"], h["spans"])]


# ---------------------------------------------------------------- label


def label_prompt(pool: dict, hits: list[dict]) -> str:
    parts = []
    for i, h in enumerate(hits, 1):
        sents = "\n".join(f"{i}.{j} {p}" for j, p in enumerate(pieces_of(h), 1))
        head = h["text"][: char_spans(h["text"], h["spans"][:1])[0][0]].rstrip(": ") if h["spans"] else h["title"]
        parts.append(f"Passage {i}: {head}\n{sents}")
    return f"Question: {pool['query']}\n\n" + "\n\n".join(parts)


def cmd_label(a) -> None:
    out = a.dir / "labels.jsonl"
    done = {r["id"] for r in read_jsonl(out)}
    pools = [p for p in read_jsonl(a.dir / "pools.jsonl") if p["id"] not in done][: a.limit or None]
    spent = sum(r.get("cost_usd") or 0 for r in read_jsonl(out))

    def one(p: dict) -> dict:
        hits = top_hits(p)
        res, raw = claude_json(label_prompt(p, hits), L_SYSTEM, L_SCHEMA, a.model, "", 0.5)
        return {"id": p["id"], "query": p["query"], "hits": [[h["pack"], h["pid"]] for h in hits],
                "needed": res["needed"], "helpful": res["helpful"], "model": a.model, "cost_usd": raw.get("total_cost_usd")}

    # One batch of calls at a time, so that the cap stops new calls instead of waiting for queued ones.
    with ThreadPoolExecutor(a.jobs) as pool, open(out, "a") as f:
        for i in range(0, len(pools), a.jobs):
            for p, fut in [(p, pool.submit(one, p)) for p in pools[i : i + a.jobs]]:
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    print(f"{p['id']}: FAILED {e}", file=sys.stderr)
                    continue
                spent += r["cost_usd"] or 0
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                print(f"{p['id']}: {len(r['needed'])} needed, {len(r['helpful'])} helpful; ${spent:.2f} so far", file=sys.stderr)
            if spent > a.max_usd:
                sys.exit(f"stopped at ${spent:.2f} (--max-usd {a.max_usd})")


# ---------------------------------------------------------------- train

STOPWORDS = set("""a an and are as at be but by can could did do does for from had has have how i if in into is it its
me my of on or our so than that the their them then there these they this those to was we were what when where which who
whom why will with would you your about tell explain describe much many""".split())


def terms(s: str) -> set[str]:
    words = "".join(c.lower() if c.isalnum() else " " for c in s).split()
    return {w[:6] for w in words if w not in STOPWORDS}


def approx_tokens(s: str) -> int:
    b = s.encode()
    d = sum(c in b"0123456789" for c in b)
    return -(-(len(b) - d) // 4) + d


def overlap_window(pieces: list[str], q: set[str], max_tokens: int = 120) -> list[int]:
    """Python twin of `evidence::sentence_window`: the indexes of the pieces it keeps."""
    if approx_tokens(" ".join(pieces)) <= max_tokens:
        return list(range(len(pieces)))
    scored = sorted(((len(terms(p) & q) + (0.5 if i == 0 else 0), i) for i, p in enumerate(pieces)), key=lambda x: -x[0])
    pick, used = [], 0
    for _, i in scored:
        n = approx_tokens(pieces[i])
        if len(pick) >= 2 and used + n > max_tokens:
            continue
        pick.append(i)
        used += n
        if len(pick) >= 4 or used >= max_tokens:
            break
    return sorted(pick)


def select_overlap(qs: str, hits: list[list[str]], budget: int) -> set[tuple[int, int]]:
    q = terms(qs)
    used, out = 0, set()
    for i, ps in enumerate(hits):
        keep = overlap_window(ps, q)
        cost = approx_tokens(" ".join(ps[j] for j in keep)) + 12
        if used + cost > budget and out:
            continue
        used += cost
        out |= {(i, j) for j in keep}
    return out


def select_head(hits: list[list[str]], scores: list[np.ndarray], budget: int, floor: float = -1e9) -> set[tuple[int, int]]:
    """Python twin of the core's head selection: the best pieces of all passages until the budget is full."""
    cand = sorted(((s, i, j) for i, sc in enumerate(scores) for j, s in enumerate(sc) if not np.isnan(s)), key=lambda x: -x[0])
    used, out, opened = 0, set(), set()
    for s, i, j in cand:
        if s < floor and out:
            break
        cost = approx_tokens(hits[i][j]) + 1 + (12 if i not in opened else 0)
        if used + cost > budget and out:
            continue
        used += cost
        opened.add(i)
        out.add((i, j))
    return out


def features(a, pools: dict, labels: list[dict]):
    """One row per labeled piece and encoder: [mean of the piece's token states, CLS state]; NaN when truncated."""
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(ETTIN / "tokenizer.json"))
    tok.enable_truncation(MAX_LEN, strategy="only_second")
    tok.no_padding()
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = a.threads
    out = {}
    for m in MODELS:
        cache = a.dir / f"feats-{Path(m).stem}.npz"
        feats = np.load(cache, allow_pickle=True)["x"].item() if cache.exists() else {}
        todo = [lab for lab in labels if any((lab["id"], p, q) not in feats for p, q in lab["hits"])]
        out[m] = feats
        if not todo:
            continue
        sess = ort.InferenceSession(str(ETTIN / m), opts, providers=["CPUExecutionProvider"])
        for n, lab in enumerate(todo):
            by = {(h["pack"], h["pid"]): h for h in pools[lab["id"]]["hits"]}
            for pack, pid in lab["hits"]:
                h = by[(pack, pid)]
                enc = tok.encode(lab["query"], h["text"])
                hs = sess.run(["last_hidden_state"], {"input_ids": np.array([enc.ids], np.int64),
                                                       "attention_mask": np.array([enc.attention_mask], np.int64)})[0][0]
                starts = np.array([o[0] if s == 1 else -1 for o, s in zip(enc.offsets, enc.sequence_ids)])
                rows = []
                for x, y in char_spans(h["text"], h["spans"]):
                    sel = (starts >= x) & (starts < y)
                    mean = hs[sel].mean(axis=0) if sel.any() else np.full(hs.shape[1], np.nan, np.float32)
                    rows.append(np.concatenate([mean, hs[0]]))
                feats[(lab["id"], pack, pid)] = np.array(rows, np.float32)
            if n % 100 == 0:
                print(f"{m}: {n}/{len(todo)}", file=sys.stderr)
        np.savez(cache, x=np.array(feats, dtype=object))
    return out


def targets(lab: dict, i: int, n: int, helpful: float) -> np.ndarray:
    t = np.zeros(n, np.float32)
    for key, v in (("helpful", helpful), ("needed", 1.0)):
        for s in lab[key]:
            p, _, j = s.partition(".")
            if p.isdigit() and j.isdigit() and int(p) == i + 1 and 1 <= int(j) <= n:
                t[int(j) - 1] = max(t[int(j) - 1], v)
    return t


class Head:
    """[mean, cls] → Linear(hidden) → GELU → Linear(1); hidden 0 is one linear layer on the mean alone."""

    def __init__(self, d: int, hidden: int):
        import torch

        self.hidden = hidden
        if hidden:
            self.net = torch.nn.Sequential(torch.nn.Linear(2 * d, hidden), torch.nn.GELU(), torch.nn.Linear(hidden, 1))
        else:
            self.net = torch.nn.Linear(d, 1)
        self.d = d

    def inputs(self, x):
        return x if self.hidden else x[:, : self.d]

    def fit(self, x: np.ndarray, y: np.ndarray, epochs: int, wd: float) -> None:
        import torch

        xt, yt = torch.tensor(self.inputs(x)), torch.tensor(y)
        opt = torch.optim.AdamW(self.net.parameters(), lr=3e-3, weight_decay=wd)
        g = torch.Generator().manual_seed(0)
        for _ in range(epochs):
            for idx in torch.randperm(len(xt), generator=g).split(512):
                opt.zero_grad()
                loss = torch.nn.functional.binary_cross_entropy_with_logits(self.net(xt[idx]).squeeze(1), yt[idx])
                loss.backward()
                opt.step()

    def score(self, x: np.ndarray) -> np.ndarray:
        import torch

        with torch.no_grad():
            return self.net(torch.tensor(self.inputs(x))).squeeze(1).numpy()

    def export(self) -> dict:
        ps = [p.detach().numpy() for p in self.net.parameters()]
        r = lambda a: [round(float(v), 5) for v in np.ravel(a)]  # noqa: E731
        if self.hidden:
            return {"hidden": self.hidden, "w1": r(ps[0]), "b1": r(ps[1]), "w2": r(ps[2]), "b2": r(ps[3])[0]}
        return {"hidden": 0, "w2": r(ps[0]), "b2": r(ps[1])[0]}


def auc(s: np.ndarray, y: np.ndarray) -> float:
    pos, neg = s[y > 0.75], s[y < 0.25]
    if not len(pos) or not len(neg):
        return float("nan")
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def cmd_train(a) -> None:
    import torch

    torch.manual_seed(0)
    torch.set_num_threads(a.threads)
    pools = {p["id"]: p for p in read_jsonl(a.dir / "pools.jsonl")}
    labels = [lab for lab in read_jsonl(a.dir / "labels.jsonl") if lab["id"] in pools]
    feats = features(a, pools, labels)
    rng = np.random.default_rng(0)
    val_ids = set(rng.choice([lab["id"] for lab in labels], size=len(labels) // 5, replace=False).tolist())

    def rows(ids: set[str], models: list[str]):
        xs, ys = [], []
        for m in models:
            for lab in labels:
                if lab["id"] not in ids:
                    continue
                for i, (pack, pid) in enumerate(lab["hits"]):
                    x = feats[m][(lab["id"], pack, pid)]
                    ok = ~np.isnan(x[:, 0])
                    xs.append(x[ok])
                    ys.append(targets(lab, i, len(x), a.helpful)[ok])
        return np.concatenate(xs), np.concatenate(ys)

    all_ids = {lab["id"] for lab in labels}
    train_ids = all_ids - val_ids
    xtr, ytr = rows(train_ids, MODELS)
    print(f"{len(labels)} questions ({len(train_ids)} train, {len(val_ids)} held out); {len(xtr) // len(MODELS)} train pieces, "
          f"{(ytr > 0.75).mean():.3f} needed")
    trunc = [(np.isnan(feats[MODELS[0]][(lab["id"], p, q)][:, 0]), targets(lab, i, len(feats[MODELS[0]][(lab["id"], p, q)]), 0))
             for lab in labels for i, (p, q) in enumerate(lab["hits"])]
    cut = np.concatenate([c for c, _ in trunc])
    need = np.concatenate([t for _, t in trunc]) > 0.75
    print(f"pieces past the {MAX_LEN}-token cut: {cut.mean():.3f} of all, {cut[need].mean():.3f} of the needed ones")

    def evaluate(head: Head, ids: set[str], m: str) -> dict:
        r = {"auc": [], "budgets": {}}
        x, y = rows(ids, [m])
        r["auc"] = auc(head.score(x) if head else -np.zeros(len(x)), y)
        for b in a.budgets:
            got_full = got_need = n_need = toks = 0
            for lab in labels:
                if lab["id"] not in ids:
                    continue
                by = {(h["pack"], h["pid"]): h for h in pools[lab["id"]]["hits"]}
                hits = [pieces_of(by[(p, q)]) for p, q in lab["hits"]]
                need = {(i, j) for i in range(len(hits)) for j, v in enumerate(targets(lab, i, len(hits[i]), 0)) if v > 0.75}
                if head:
                    scores = []
                    for p, q in lab["hits"]:
                        fx = feats[m][(lab["id"], p, q)]
                        s = np.full(len(fx), np.nan)
                        ok = ~np.isnan(fx[:, 0])
                        if ok.any():
                            s[ok] = head.score(fx[ok])
                        scores.append(s)
                    sel = select_head(hits, scores, b)
                else:
                    sel = select_overlap(lab["query"], hits, b)
                toks += sum(approx_tokens(hits[i][j]) for i, j in sel)
                got_need += len(need & sel)
                n_need += len(need)
                got_full += need <= sel
            n = len(ids)
            r["budgets"][b] = (got_need / max(n_need, 1), got_full / n, toks / n)
        return r

    def show(name: str, r: dict) -> None:
        cells = "  ".join(f"{b}: rec {v[0]:.3f} all {v[1]:.3f} tok {v[2]:.0f}" for b, v in r["budgets"].items())
        print(f"{name:28s} auc {r["auc"]:.4f}  {cells}")

    for m in MODELS:
        show(f"word overlap ({Path(m).stem})", evaluate(None, val_ids, m))
    best = None
    for hidden in a.hidden:
        head = Head(feats[MODELS[0]][next(iter(feats[MODELS[0]]))].shape[1] // 2, hidden)
        head.fit(xtr, ytr, a.epochs, a.wd)
        for m in MODELS:
            r = evaluate(head, val_ids, m)
            show(f"head h={hidden} ({Path(m).stem})", r)
        if best is None or r["auc"] > best[0]:
            best = (r["auc"], hidden)
    hidden = a.final_hidden if a.final_hidden is not None else best[1]
    head = Head(feats[MODELS[0]][next(iter(feats[MODELS[0]]))].shape[1] // 2, hidden)
    xall, yall = rows(all_ids, MODELS)
    head.fit(xall, yall, a.epochs, a.wd)
    out = ROOT / "core/commonplace-core/src/sentence_head.json"
    out.write_text(json.dumps(head.export()) + "\n")
    print(f"wrote {out.relative_to(ROOT)} (hidden {hidden}, trained on all {len(labels)} questions)")


# ---------------------------------------------------------------- compare

GRADE = {"full": 2, "partial": 1, "none": 0}


def sign_p(w: int, l: int) -> float:
    from math import comb

    n = w + l
    return min(1.0, 2 * sum(comb(n, i) for i in range(min(w, l) + 1)) / 2**n) if n else 1.0


def cmd_compare(a) -> None:
    runs = Path(__import__("os").environ.get("EVAL_RUNS") or ROOT / "eval/runs")
    load = lambda r: {x["id"]: x["sufficiency"] for x in read_jsonl(runs / r / "evidence.jsonl")}  # noqa: E731
    base = load(a.base)
    rows = []
    for r in [a.base, *a.runs]:
        o = load(r)
        ids = [i for i in base if i in o]
        toks = [x["record"]["evidence_tokens"] for x in read_jsonl(runs / r / "answers.jsonl") if x["id"] in o]
        w = sum(GRADE[o[i]] > GRADE[base[i]] for i in ids)
        l = sum(GRADE[o[i]] < GRADE[base[i]] for i in ids)
        rows.append({"run": r, "n": len(ids), "full": sum(o[i] == "full" for i in ids), "none": sum(o[i] == "none" for i in ids),
                     "mean_grade": round(sum(GRADE[o[i]] for i in ids) / len(ids), 3), "evidence_tokens": round(sum(toks) / len(toks)),
                     "better": w, "worse": l, "sign_p": round(sign_p(w, l), 3)})
        print(f"{r:32s} n {len(ids)}  full {rows[-1]['full']}  none {rows[-1]['none']}  grade {rows[-1]['mean_grade']:.2f}  "
              f"tokens {rows[-1]['evidence_tokens']}  vs base: better {w} worse {l} (p {rows[-1]['sign_p']:.2f})")
    if a.out:
        write_jsonl(a.out, rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=ROOT / "data/work/sentsel")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("questions")
    q.add_argument("--per-topic", type=int, default=30)
    q.add_argument("--model", default="sonnet")
    q.add_argument("--jobs", type=int, default=4)
    p = sub.add_parser("pool")
    p.add_argument("--library", default="data/library")
    lb = sub.add_parser("label")
    lb.add_argument("--model", default="sonnet")
    lb.add_argument("--jobs", type=int, default=4)
    lb.add_argument("--limit", type=int, default=0)
    lb.add_argument("--max-usd", type=float, default=9.0)
    t = sub.add_parser("train")
    t.add_argument("--threads", type=int, default=8)
    t.add_argument("--hidden", type=int, nargs="+", default=[0, 32])
    t.add_argument("--final-hidden", type=int)
    t.add_argument("--epochs", type=int, default=40)
    t.add_argument("--wd", type=float, default=1e-2)
    t.add_argument("--helpful", type=float, default=0.5, help="target of a helpful piece (needed is 1)")
    t.add_argument("--budgets", type=int, nargs="+", default=[300, 400, 500, 600])
    c = sub.add_parser("compare")
    c.add_argument("--base", required=True)
    c.add_argument("--runs", nargs="+", required=True)
    c.add_argument("--out", type=Path)
    a = ap.parse_args()
    a.dir.mkdir(parents=True, exist_ok=True)
    {"questions": cmd_questions, "pool": cmd_pool, "label": cmd_label, "train": cmd_train, "compare": cmd_compare}[a.cmd](a)


if __name__ == "__main__":
    main()
