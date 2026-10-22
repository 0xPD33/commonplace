# eval

The eval harness. It measures our answers against Claude with web search. [docs/EVAL.md](../docs/EVAL.md) holds the current report.

## Files

| Path | What it holds |
|---|---|
| `queries.jsonl` | The 63 seed questions, in nine categories. I wrote 16 and Claude drafted 47. |
| `dev.jsonl`, `test.jsonl` | The splits that `split.py` makes. They do not exist yet: `split.py` needs more than 100 questions. `run.py` and `baseline.py` default to `dev.jsonl`, so pass `--queries eval/queries.jsonl`. |
| `smoke.jsonl` | Three plumbing questions. They are not part of the eval. |
| `conversation.jsonl` | 92 multi-turn threads for tuning: follow-ups, requests for more, compare follow-ups, topic switches, reformat requests ("make it shorter") and small talk. The first 60 have the 2026-09-30 and 2026-10-10 "before" runs. The judge sees the earlier turns. |
| `conversation-test.jsonl` | 30 more threads of the same kinds. Locked: run it to report, never to tune. |
| `baselines/<id>.json` | One Claude baseline answer per query, with cited URLs, model and cost. |
| `gold.jsonl` | Gold articles per query, from `gold.py` (for inspection). |
| `runs/<run-id>/` | `meta.json`, `answers.jsonl`, `recall.jsonl`, `judgments.jsonl`, `pairwise-vs-<run>.jsonl`, `per_query.jsonl`, `stderr.log`. Git ignores `runs/`. Only the runs that the docs cite are in the repository (see below). Set `EVAL_RUNS=<dir>` to keep the runs elsewhere; every script reads it. |
| `history.csv` | One row per reported run. |
| `common.py` | Shared paths and the `claude -p` wrapper. |

## Cited runs

| Run | What it is | Score ratio |
|---|---|---|
| `v22-answer-rules` | Seeds, the 0.2.4 changes with the phone's settings and the Starter set: general knowledge without citations, the underline check, check turns. See docs/BENCHMARKS.md (2026-10-22). | 0.533 |
| `v21-link-fix` | Seeds, the control of that comparison: 0.2.3 and the "Should I" link fix only. | 0.546 |
| `v20-phone-starter` | Seeds, a rejected try: general knowledge marked with [0]. Ling never wrote the mark. | 0.512 |
| `v19-phone-starter` | Seeds, the 0.2.0 build with the phone's settings (`--phone`) and the Starter set. It is the source of `docs/EVAL.md`. | 0.530 |
| `v18-release` | Seeds, the 0.1 release build with all packs: clean passage text, entity linking by the other names in the question, no stray citation labels. | 0.529 |
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
  `compare_followup`, `topic_switch`, `reformat` and `chat`.
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
nix develop -c python3 eval/evidence.py --run <run-id>   # optional: retrieval or generator failure?
nix develop -c python3 eval/pairwise.py --a <run-id> --b <other-run-id>
```

- `run.py --phone` runs what the phone ships: `--fuse-keep=24`, the quantized encoders
  (`--leaf-model=onnx/model_quantized.onnx`, `--ettin-model=onnx/model_sel8.onnx`) and the GGUF of the
  library's own model pack. Choose the pack set with `--library <dir>` and the build with `--bin`.
  Export `ORT_DYLIB_PATH` first (the devshell sets it). `meta.json` records the flags, the binary
  path and its sha256, and the model. `recall.py` reuses all of them.
- The reranker files come from `data/models/ettin-17m/`. Since 2026-10-13, `scripts/fetch-models.sh
  encoders` puts the distilled reranker there, so the desktop CLI and `--phone` use it. Runs from
  before that date used the stock Ettin-17m: compare a new run with a new baseline run, not with them.
- Each answer record holds `evidence`: the sources the model read, as `{n, pack_id, title, section,
  text}`, with the exact text it saw, and `extra` for the Wikidata and COMPUTED lines. A binary
  built before this change leaves `evidence` out. Compare `evidence` with the gold articles to tell a
  retrieval failure from a generator failure.
- `run.py` takes `--model <gguf>`, `--library <dir>` (default `data/library`), `--think`,
  the ablation flags (`--no-dense`, `--no-sparse`, `--no-rerank`, `--no-cards`,
  `--no-doc2query`, `--no-llm`) and `--subset` (query ids or categories, comma-separated).
  `--cli=<flag>` passes a flag to `commonplace`, for example `--cli=--rewrite`. The question
  rewrite step is off by default. `run.py`, `baseline.py` and `judge.py` resume where they stopped.
- `run.py --live-history` answers the earlier turns of `followup`, `expand`,
  `compare_followup` and `topic_switch` threads with the system first, so the history holds our own answers and
  sources, as in the app. Reformat and small-talk threads keep the fixed turns, because their
  baselines rewrite or answer that fixed text. `--bin` runs another `commonplace` binary.
- `baseline.py` and `judge.py` use `claude --model opus` by default. Change it with `--model`.
  `--budget-usd` caps the cost of each call.
- `recall.py` writes three kinds of recall per query. `recall@10` and `recall@40` count gold articles
  from any pack: the baseline's Wikipedia pages, the baseline URLs that open a page of another pack
  (Wikivoyage, Stack Exchange, Arch Wiki, MedlinePlus, CDC, arXiv, DevDocs, ...) and
  `expected_sources`. An `expected_sources` entry is a title or `{"pack": "<id>", "title": "<title>"}`.
  `recall_wiki@10` and `@40` use Wikipedia gold only, as before. `recall@ev` counts the gold articles
  among the sources the model saw. A follow-up thread is searched with the text that its answer
  searched. Chat and reformat turns do not search, so they get no recall. The baselines cite few
  pages that another pack holds (1 of 153 queries), so for most queries only `expected_sources` or
  the `evidence.py` labels see a hit outside Wikipedia. `recall@ev` leaves out Wikidata sources,
  because their titles match Wikipedia gold titles. `recall.py --library <dir>` searches another
  library than the run's, and `--out <file>` names the output file. `gold.py --wikipedia-only` uses
  the old method.
- `pairwise.py` compares two of our runs, not our run with the baseline. It uses the rubric and
  prompt of `judge.py`, swaps A and B for every query and shows the judge neither run id. It prints
  win / tie / loss of run A, a two-sided exact sign test on the decided queries and the mean score
  difference with a bootstrap 95% interval. Use it when the ratio judge (+-0.05) cannot see the
  gain. Identical answers count as ties and cost nothing: decoding is greedy, so two builds often
  answer most queries alike (58 of 63 seeds for `cp-master` and `cp-final`). The ratio judge still
  scores such pairs differently (sd 0.44 points per query, about 0.006 of the ratio over 63 queries).
  `--subset` and `--model` work as in `judge.py`. The judge sees the fixed earlier turns of a
  thread, not each run's own.
- `evidence.py` asks Claude (`--model sonnet` by default) whether the evidence that the model saw can
  answer the question: `full`, `partial` or `none`, the relevant passages and what is missing. It writes
  `evidence.jsonl`, and `report.py` splits the lost queries into generator failures (full evidence)
  and retrieval failures (partial or no evidence). It needs a run from a binary that records `evidence`.
- `report.py --history <csv>` writes the row to another CSV, and `--out <file>` the report.
- Run `baseline.py` before `recall.py` and `judge.py`. It only fetches missing baselines.
- Run `conversation-test.jsonl` only to report. Do not tune on its failures.
