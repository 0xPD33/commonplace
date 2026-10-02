# eval

The eval harness for PLAN §12. It measures our answers against Claude with web search.

## Files

| Path | What it holds |
|---|---|
| `queries.jsonl` | The full query set. Paddy writes it (PLAN §12.1). |
| `dev.jsonl`, `test.jsonl` | The splits from `split.py`. `test.jsonl` is locked. |
| `smoke.jsonl` | Three plumbing questions. They are not part of the eval. |
| `conversation.jsonl` | 60 multi-turn threads for tuning: follow-ups, requests for more, compare follow-ups, reformat requests ("make it shorter") and small talk. The judge sees the earlier turns. |
| `conversation-test.jsonl` | 30 more threads of the same kinds. Locked: run it to report, never to tune. |
| `baselines/<id>.json` | One Claude baseline answer per query, with cited URLs, model and cost. |
| `gold.jsonl` | Gold articles per query, from `gold.py` (for inspection). |
| `runs/<run-id>/` | `meta.json`, `answers.jsonl`, `recall.jsonl`, `judgments.jsonl`, `per_query.jsonl`, `stderr.log`. |
| `history.csv` | One row per reported run. |
| `common.py` | Shared paths and the `claude -p` wrapper. |

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
nix develop -c python3 eval/split.py                     # once, after queries.jsonl is final
nix develop -c python3 eval/run.py --queries eval/dev.jsonl --label base
nix develop -c python3 eval/baseline.py --queries eval/dev.jsonl
nix develop -c python3 eval/gold.py --queries eval/dev.jsonl
nix develop -c python3 eval/recall.py --run <run-id>
nix develop -c python3 eval/judge.py --run <run-id>
nix develop -c python3 eval/report.py --run <run-id>     # writes docs/EVAL.md
```

- `run.py` takes `--model <gguf>`, `--library <dir>` (default `data/library`), `--deep`, the
  ablation flags (`--no-dense`, `--no-sparse`, `--no-rerank`,
  `--no-cards`, `--no-planner`, `--no-doc2query`, `--no-llm`) and `--subset` (query ids or
  categories, comma-separated). `run.py`, `baseline.py` and `judge.py` resume where they
  stopped.
- `run.py --live-history` answers the earlier turns of `followup`, `expand` and
  `compare_followup` threads with the system first, so the history holds our own answers and
  sources, as in the app. Reformat and small-talk threads keep the fixed turns, because their
  baselines rewrite or answer that fixed text. `--bin` runs another `commonplace` binary.
- `baseline.py` and `judge.py` use `claude --model opus` by default. Change it with `--model`.
  `--budget-usd` caps the cost of each call.
- Run `baseline.py` before `recall.py` and `judge.py`. It only fetches missing baselines.
- Run the test split only at milestone ends (M3, M4, M5). Do not tune on test failures.
