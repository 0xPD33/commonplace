# eval

The eval harness. It measures our answers against Claude with web search. [docs/EVAL.md](../docs/EVAL.md) holds the current report.

## Files

| Path | What it holds |
|---|---|
| `queries.jsonl` | The 63 seed questions, in nine categories. I wrote 16 and Claude drafted 47. |
| `dev.jsonl`, `test.jsonl` | The splits that `split.py` makes. They do not exist yet: `split.py` needs more than 100 questions. `run.py` and `baseline.py` default to `dev.jsonl`, so pass `--queries eval/queries.jsonl`. |
| `smoke.jsonl` | Three plumbing questions. They are not part of the eval. |
| `conversation.jsonl` | 60 multi-turn threads for tuning: follow-ups, requests for more, compare follow-ups, reformat requests ("make it shorter") and small talk. The judge sees the earlier turns. |
| `conversation-test.jsonl` | 30 more threads of the same kinds. Locked: run it to report, never to tune. |
| `baselines/<id>.json` | One Claude baseline answer per query, with cited URLs, model and cost. |
| `gold.jsonl` | Gold articles per query, from `gold.py` (for inspection). |
| `runs/<run-id>/` | `meta.json`, `answers.jsonl`, `recall.jsonl`, `judgments.jsonl`, `per_query.jsonl`, `stderr.log`. Git ignores `runs/`. Only the runs that the docs cite are in the repository (see below). |
| `history.csv` | One row per reported run. |
| `common.py` | Shared paths and the `claude -p` wrapper. |

## Cited runs

| Run | What it is | Score ratio |
|---|---|---|
| `v18-release` | Seeds, the release build: clean passage text, entity linking by the other names in the question, no stray citation labels. It is the source of `docs/EVAL.md`. | 0.529 |
| `v17-norewrite` | Seeds, 2026 library, rewrite off | 0.521 |
| `v16-enwiki2026` | Seeds, 2026 library, rewrite on | 0.508 |
| `v15-live-all-dense` | Seeds, 2023 library with all breadth packs | 0.464 |
| `cv2-dev-base` | The 60 tuning threads (2026-09-30) | 0.616 |
| `cv2-dev-intent` | The 60 tuning threads with the question-intent head | 0.615 |
| `cv2-test-base` | The 30 locked test threads (2026-09-30) | 0.614 |

The conversation runs used `--live-history` and the 2023 library. Judge noise is about +-0.05 on 63 questions.

## Query schema

One JSON object per line:

```json
{"id": "cmp-07", "query": "…", "category": "comparison", "difficulty": 2,
 "time_sensitive": false, "expected_sources": [], "history": []}
```

- `category`: `factual`, `explanation`, `comparison`, `multihop`, `numeric`, `travel`,
  `howto`, `out_of_corpus` or `long_tail`. The conversation files use `followup`, `expand`,
  `compare_followup`, `reformat` and `chat`.
- `difficulty`: 1 to 3.
- `expected_sources`: Wikipedia article titles. When the list is empty, the gold set comes
  from the baseline.
- `history` (optional): earlier turns as `{"query", "answer"}` for follow-up questions.

## Commands

Run each command from the repo root. Python exists only in the devshell.

```sh
nix develop -c python3 eval/run.py --queries eval/queries.jsonl --run-id <run-id>
nix develop -c python3 eval/baseline.py --queries eval/queries.jsonl
nix develop -c python3 eval/gold.py --queries eval/queries.jsonl
nix develop -c python3 eval/recall.py --run <run-id>
nix develop -c python3 eval/judge.py --run <run-id>
nix develop -c python3 eval/report.py --run <run-id>     # writes docs/EVAL.md and a row in history.csv
```

- `run.py` takes `--model <gguf>`, `--library <dir>` (default `data/library`), `--deep`, `--think`,
  the ablation flags (`--no-dense`, `--no-sparse`, `--no-rerank`, `--no-cards`, `--no-planner`,
  `--no-doc2query`, `--no-llm`) and `--subset` (query ids or categories, comma-separated).
  `--cli=<flag>` passes a flag to `commonplace`, for example `--cli=--rewrite`. The question
  rewrite step is off by default. `run.py`, `baseline.py` and `judge.py` resume where they stopped.
- `run.py --live-history` answers the earlier turns of `followup`, `expand` and
  `compare_followup` threads with the system first, so the history holds our own answers and
  sources, as in the app. Reformat and small-talk threads keep the fixed turns, because their
  baselines rewrite or answer that fixed text. `--bin` runs another `commonplace` binary.
- `baseline.py` and `judge.py` use `claude --model opus` by default. Change it with `--model`.
  `--budget-usd` caps the cost of each call.
- Run `baseline.py` before `recall.py` and `judge.py`. It only fetches missing baselines.
- Run `conversation-test.jsonl` only to report. Do not tune on its failures.
