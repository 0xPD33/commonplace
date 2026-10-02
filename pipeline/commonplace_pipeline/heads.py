"""Train a classifier head: a softmax layer over the query encoder's embedding.

  turn-kind  a chat message: question (search and answer), reformat ("make it shorter") or chat
             (small talk)

Examples: pipeline/configs/<name>-examples.jsonl (hand-written) and pipeline/configs/generated/<name>-*.jsonl
(model-written), one {"text", "kind"} per line. The label of the first hand-written line is the default.
An example that repeats an eval question (eval/queries.jsonl, eval/conversation*.jsonl) is dropped, so
the eval stays a test: the same text, or a cosine of `--near` or more. Short chat messages ("thanks!")
are near each other by nature, so the turn-kind head uses `--near 1` (same text only). Each example is embedded twice
by `commonplace encode`, with the desktop (fp32) and the phone (int8) encoder, so the head fits both.
Writes core/commonplace-core/src/<name>_head.json (dashes become underscores); the core compiles it in.

  uv run --project pipeline python -m commonplace_pipeline.heads turn-kind \\
      --eval eval/conversation.jsonl --map followup=question,compare_followup=question,expand=question,reformat=reformat,chat=chat
  uv run --project pipeline python -m commonplace_pipeline.heads intent --strict calc \\
      --eval eval/queries.jsonl --map factual=lookup,explanation=explain,comparison=compare,numeric=calc

`--strict` names the labels whose mistakes are costly (default: every label but the first). The engine
takes a strict label only at `min_prob` or above, else the most likely other label. `min_prob` is set from
cross-validation so that no held-out example of another label would have been taken for a strict one.
`--eval` only reports; it never trains.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / "core/target/release/commonplace"
MODELS = ["onnx/model.onnx", "onnx/model_quantized.onnx"]  # desktop, phone
EVAL_FILES = ["eval/queries.jsonl", "eval/conversation.jsonl", "eval/conversation-test.jsonl"]


def same(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalnum() or c == " ").strip()


def embed(texts: list[str], model: str) -> np.ndarray:
    out = subprocess.run([str(BIN), "--leaf-model", model, "encode"], input="\n".join(texts) + "\n",
                         capture_output=True, text=True, check=True, cwd=ROOT)
    return np.array([json.loads(line) for line in out.stdout.splitlines()], dtype=np.float32)


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def fit(x: np.ndarray, y: np.ndarray, k: int, l2: float, steps: int = 4000, lr: float = 4.0) -> tuple[np.ndarray, np.ndarray]:
    """Multinomial logistic regression, full-batch gradient descent (inputs are unit vectors)."""
    w = np.zeros((k, x.shape[1]))
    b = np.zeros(k)
    t = np.eye(k)[y]
    for _ in range(steps):
        g = (softmax(x @ w.T + b) - t) / len(x)
        w -= lr * (g.T @ x + l2 * w)
        b -= lr * g.sum(axis=0)
    return w, b


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="head name, e.g. turn-kind")
    ap.add_argument("--l2", type=float, default=1e-3)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--eval", help="query JSONL to report on")
    ap.add_argument("--map", default="", help="category=label pairs for --eval; other categories are skipped")
    ap.add_argument("--strict", help="comma list of labels that need min_prob (default: all but the first)")
    ap.add_argument("--near", type=float, default=0.80, help="drop examples at this cosine to an eval question or above")
    a = ap.parse_args()

    files = [ROOT / f"pipeline/configs/{a.name}-examples.jsonl", *sorted((ROOT / "pipeline/configs/generated").glob(f"{a.name}-*.jsonl"))]
    rows, seen = [], set()
    for f in files:
        for r in (json.loads(line) for line in f.read_text().splitlines() if line.strip()):
            key = r["text"].strip().lower()
            if key not in seen:
                seen.add(key)
                rows.append(r)
    evals = [json.loads(line)["query"] for f in EVAL_FILES for line in (ROOT / f).read_text().splitlines() if line.strip()]
    near = (embed([r["text"] for r in rows], MODELS[1]) @ embed(evals, MODELS[1]).T).max(axis=1)
    evals_same = {same(t) for t in evals}
    keep = [n < a.near and same(r["text"]) not in evals_same for r, n in zip(rows, near)]
    print(f"{len(rows)} examples from {len(files)} files; {keep.count(False)} dropped as repeats of eval questions")
    rows = [r for r, k in zip(rows, keep) if k]
    labels = list(dict.fromkeys(r["kind"] for r in rows))
    texts = [r["text"] for r in rows]
    y = np.array([labels.index(r["kind"]) for r in rows])
    strict = a.strict.split(",") if a.strict else labels[1:]
    assert set(strict) < set(labels), f"--strict must name some, not all, of {labels}"
    is_strict = np.array([lab in strict for lab in labels])
    embs = [embed(texts, m) for m in MODELS]
    print(f"{len(rows)} examples: " + ", ".join(f"{lab} {int((y == i).sum())}" for i, lab in enumerate(labels)))

    # Cross-validation by example, so both encodings of a text stay in the same fold.
    fold = np.random.default_rng(0).permutation(len(rows)) % a.folds
    probs = np.zeros((len(MODELS), len(rows), len(labels)))
    for k in range(a.folds):
        tr, te = fold != k, fold == k
        w, b = fit(np.concatenate([e[tr] for e in embs]), np.concatenate([y[tr]] * len(MODELS)), len(labels), a.l2)
        for i, e in enumerate(embs):
            probs[i, te] = softmax(e[te] @ w.T + b)
    pred = probs.argmax(axis=2)
    for i, m in enumerate(MODELS):
        print(f"cross-validation accuracy ({m}): {(pred[i] == y).mean():.3f}")
    # Taking another label's example for a strict label is the costly error; it sets the bar.
    wrong = [probs[i, j, pred[i, j]] for i in range(len(MODELS)) for j in range(len(rows)) if not is_strict[y[j]] and is_strict[pred[i, j]]]
    min_prob = round(float(max(wrong, default=0.5)) + 0.01, 3)
    print(f"held-out examples taken for a strict label ({','.join(strict)}): {len(wrong)}; min_prob = {min_prob}")

    def decide(p: np.ndarray) -> str:
        """The engine's rule: the most likely label, unless it is strict and below min_prob."""
        return next(labels[k] for k in np.argsort(-p) if not is_strict[k] or p[k] >= min_prob)
    for i, m in enumerate(MODELS):
        for j in np.where(pred[i] != y)[0]:
            print(f"  miss ({m}): {texts[j]!r} is {labels[y[j]]}, predicted {labels[pred[i, j]]} {probs[i, j].max():.2f}")

    w, b = fit(np.concatenate(embs), np.concatenate([y] * len(MODELS)), len(labels), a.l2)
    out = ROOT / f"core/commonplace-core/src/{a.name.replace('-', '_')}_head.json"
    out.write_text(json.dumps({"labels": labels, "strict": strict, "min_prob": min_prob, "b": [round(float(v), 5) for v in b],
                               "w": [[round(float(v), 5) for v in row] for row in w]}) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")

    if a.eval:
        want = dict(pair.split("=") for pair in a.map.split(",") if pair)
        qs = [q for q in (json.loads(line) for line in (ROOT / a.eval).read_text().splitlines() if line.strip())
              if q.get("category") in want]
        for m in MODELS:
            p = softmax(embed([q["query"] for q in qs], m) @ w.T + b)
            ok = best = 0
            for q, pr in zip(qs, p):
                got = decide(pr)
                ok += got == want[q["category"]]
                best += labels[int(pr.argmax())] == want[q["category"]]
                if got != want[q["category"]]:
                    print(f"  eval miss ({m}): {q['query']!r} want {want[q['category']]}, got {got} {pr.max():.2f}")
            print(f"eval ({m}): {ok}/{len(qs)} right at min_prob {min_prob}; best guess {best}/{len(qs)}")


if __name__ == "__main__":
    main()
