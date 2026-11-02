# eval

The eval harness. It measures our answers against Claude with web search. [docs/EVAL.md](../docs/EVAL.md) holds the current report.

## Files

| Path | What it holds |
|---|---|
| `queries.jsonl` | The 120 seed questions, in nine categories. I wrote 16; Claude drafted 47 and, on 2026-11-01, 57 more in the weak categories (factual, numeric, long tail). |
| `dev.jsonl`, `test.jsonl` | The seed splits that `split.py` makes (`--test-size 60`). `test.jsonl` is locked: run it to report, never to tune. `run.py` and `baseline.py` default to `dev.jsonl`. |
| `canary.jsonl` | 12 questions with an `expect` answer. Every one must pass on every backend: the release gate. |
| `abstention.jsonl` | 30 questions: 15 that the library cannot answer (`behavior: abstain`) and 15 with a false premise (`behavior: premise`). `abstain.py` grades them. |
| `robust.jsonl` | 20 misspelled, spoken or misheard versions of seeds. Each borrows the baseline of its clean seed (`baseline`). |
| `recent.jsonl` | 20 questions on events from 2025 and 2026 that the September 2026 snapshot covers and the models do not know, each with an `expect` answer. The closed-book run gets none right: this set measures what the library adds. |
| `practical.jsonl` | 20 "how do I" questions (household fixes, first aid, cooking). The library is weakest here: the closed-book run beats it on 15 of 20. |
| `smoke.jsonl` | Three plumbing questions. They are not part of the eval. |
| `conversation.jsonl` | 92 multi-turn threads for tuning: follow-ups, requests for more, compare follow-ups, topic switches, reformat requests ("make it shorter") and small talk. The first 60 have the 2026-09-30 and 2026-10-10 "before" runs. The judge sees the earlier turns. |
| `conversation-test.jsonl` | 30 more threads of the same kinds. Locked: run it to report, never to tune. |
| `baselines/<id>.json` | One Claude baseline answer per query, with cited URLs, model and cost. |
| `gold.jsonl` | Gold articles per query, from `gold.py` (for inspection). |
| `runs/<run-id>/` | `meta.json`, `answers.jsonl`, `recall.jsonl`, `judgments.jsonl`, `pairwise-vs-<run>.jsonl`, `per_query.jsonl`, `stderr.log`. Git ignores `runs/`. Only the runs that the docs cite are in the repository (see below). Set `EVAL_RUNS=<dir>` to keep the runs elsewhere; every script reads it. |
| `history.csv` | One row per reported run. |
| `common.py` | Shared paths and the `claude -p` wrapper. |
| `abstain.py` | The abstention judge: `abstain.jsonl` in the run directory. |
| `import.py` | Turns answers from any other system (an app, a model, a phone run) into a run: JSONL with `id` and `answer`, optionally `record` timings and citation `segments`. Then the judge and the report work as for our runs. |
| `human.py` | A person checks the judge: `make` writes a sheet of 30 of our answers to mark as correct (yes, partly or no); `score` compares the marks with the judge's correctness points. |

## Cited runs

| Run | What it is | Score ratio |
|---|---|---|
| `v2-ref-ling` | The reference run and the gate's baseline: Ling with the phone's settings, the Starter set and the Reference shelf, on test, canary, abstention, robust and recent (142 questions, 2026-11-02). The source of `docs/EVAL.md` | 0.64 |
| `v2-gemma-e2b` | The same with the Gemma 4 E2B Q4_0 GGUF (`ggml-org/gemma-4-E2B-it-GGUF`), a desktop stand-in for the NPU file | 0.60 |
| `v2-closed-ling` | The same Ling with `--closed-book`: no library | 0.47 |
| `v2-ref-ling-r2` | A second run of the reference build on the first 122 questions: identical answers, judged again | 0.591 |
| `v2-ref-ling-rejudge` | The reference answers on the first 122 questions, judged a second time | 0.592 |
| `v2-rewrite-robust` | `robust.jsonl` with `--rewrite` (the model rewrites the question before search): 5.78 against 5.22 without | 0.579 |
| `v2-prac-ling` | `practical.jsonl` with the reference settings: 4.17 of 10 on average | – |
| `v2-prac-closed` | `practical.jsonl` with `--closed-book`: 5.85, better than the library on 15 of 20 | – |
| `v2-A-ref` | The reference set with the how-to prompt (now the default) and the `wikibooks-en` pack without its alternative-medicine books. 127 of 142 answers match `v2-ref-ling`; how-to rose from 4.18 to 4.90 of 10 over 34 questions (with `v2-A-howto`). The gate failed only on unverified sentences, 11.4% against 9.6%: the model's own steps in how-to answers | 0.65 |
| `v2-A-howto` | The how-to questions of `dev.jsonl` and `practical.jsonl` with the same settings | – |
| `v2-B-ref` | `v2-A-ref` plus the `stackexchange` pack: how-to rose to 5.19, but other questions lost (pairwise 12 better, 23 worse), so the pack stays optional | 0.62 |
| `x-032-phone-full` | The 63 seeds in the 0.3.2 test app on the Pixel 10 with Gemma, the Starter set and the Reference shelf (2026-10-24). The same answers as `x-gemma4-phone-shelf`, so not judged | – |
| `x-032-phone-cv` | The 30 locked test threads in the same app; the app wrote the earlier turns (2026-10-24) | 0.605 |
| `x-gemma4-phone-shelf` | Seeds asked in the app on the Pixel 10 with Gemma, the Starter set and the Reference shelf (debug build with the number changes, 2026-10-23) | 0.511 |
| `x-gemma4-phone-num` | The 13 numeric and comparison seeds on the Pixel with the number changes, pairwise against `x-gemma4-phone2` only | – |
| `x-gemma4-d-prompt` | Seeds, the Gemma GGUF on the desktop with an answer prompt after Google's RAG sample (dropped) | 0.516 |
| `x-gemma4-d-base` | The control of that comparison: the same build with our answer prompt | 0.526 |
| `x-gemma4-d-len` | The same build with an answer target of 150 to 250 words instead of 100 to 200 (dropped) | 0.527 |
| `x-gemma4-num-fix`, `x-gemma4-num-base` | The 13 numeric and comparison seeds with the Gemma GGUF on the desktop, with and without the number changes, pairwise only | – |
| `x-gemma4-phone2` | Seeds asked in the app on the Pixel 10 with Gemma on the NPU and the full evidence (0.3.0 debug build, 2026-10-23) | 0.499 |
| `x-gemma4-phone` | The same with the warm-phone evidence cut (52 of 63 answers) | 0.516 |
| `x-gemma4-qat` | Seeds, Google's QAT Q4_0 GGUF of Gemma 4 E2B on the desktop | 0.505 |
| `x-gemma4-qat-prompt` | The same with a rule to state the COMPUTED value (dropped) | 0.511 |
| `x-gemma4-qat-ev1200` | The same with an evidence budget of 1,200 (about 1,000 tokens) | 0.479 |
| `x-pool24`, `x-pool48`, `x-pool96` | Seeds, Ling with rerank pools of 24, 48 and 96, for recall only (not judged) | – |
| `x-gemma4-cv-test` | The 30 locked test threads with the same Gemma GGUF and settings (2026-10-23) | 0.640 |
| `x-gemma4-e2b` | Seeds, Gemma 4 E2B Q4_0 (`ggml-org/gemma-4-E2B-it-GGUF`) instead of Ling, the phone's settings, the Starter set (2026-10-23). A desktop stand-in for the NPU file. | 0.523 |
| `v23-starter-shelf` | Seeds, the 0.2.4 engine with the phone's settings, the Starter set and the Reference shelf (2026-10-23). | 0.555 |
| `cv3-dev-024` | The 92 tuning threads, the 0.2.4 engine with the phone's settings and the Starter set (2026-10-23) | 0.649 |
| `cv3-test-024` | The 30 locked test threads, the same settings (2026-10-23) | 0.646 |
| `v22-answer-rules` | Seeds, the 0.2.4 changes with the phone's settings and the Starter set: general knowledge without citations, the underline check, check turns. See docs/BENCHMARKS.md (2026-10-22). It is the source of `docs/EVAL.md`. | 0.533 |
| `v21-link-fix` | Seeds, the control of that comparison: 0.2.3 and the "Should I" link fix only. | 0.546 |
| `v20-phone-starter` | Seeds, a rejected try: general knowledge marked with [0]. Ling never wrote the mark. | 0.512 |
| `v19-phone-starter` | Seeds, the 0.2.0 build with the phone's settings (`--phone`) and the Starter set. | 0.530 |
| `v18-release` | Seeds, the 0.1 release build with all packs: clean passage text, entity linking by the other names in the question, no stray citation labels. | 0.529 |
| `v17-norewrite` | Seeds, 2026 library, rewrite off | 0.521 |
| `v16-enwiki2026` | Seeds, 2026 library, rewrite on | 0.508 |
| `v15-live-all-dense` | Seeds, 2023 library with all breadth packs | 0.464 |
| `cv2-dev-base` | The 60 tuning threads (2026-09-30) | 0.616 |
| `cv2-dev-intent` | The 60 tuning threads with the question-intent head | 0.615 |
| `cv2-test-base` | The 30 locked test threads (2026-09-30) | 0.614 |

Runs before `v2-A-ref` used the general prompt for how-to questions; pass `--cli=--no-how-to-steps` to reproduce them. The `cv2` conversation runs used `--live-history` and the 2023 library. The `cv3` runs used `--live-history`, `--phone` and the Starter set. Noise, measured on 2026-11-02: on the desktop the same build gives the same answers (`v2-ref-ling-r2` matches `v2-ref-ling` on all 122). The judge, run twice on the same 80 answers, moved the score ratio by 0.002 and 4 to 7 queries by a point or more (`v2-ref-ling-rejudge`). So the bootstrap interval in the report, about ±0.05 on 80 questions, is the uncertainty that counts: it comes from the choice of questions. The Sonnet abstention judge flipped 2 of 30 verdicts on identical answers; the Opus judge flipped none, so `abstain.py` uses Opus.

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
- `expect` (optional): a deterministic check. A list of strings passes when the first sentence of the
  answer contains one (case-insensitive). `{"text": [...], "sentences": 2}` searches the first two
  sentences, for multi-hop answers that name the last hop second. `{"number": x, "tol": 0.02}` passes
  when a number anywhere in the answer is within the relative tolerance; `number` can be a list for
  questions with two common readings.
- `behavior` and `note` (abstention set): what the answer must do, and the facts for the judge.
- `baseline` (robustness set): the id whose baseline the judge uses.

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
nix develop -c python3 eval/abstain.py --run <run-id>    # the abstention set
nix develop -c python3 eval/run.py --queries <files> --phone --cli=--closed-book --run-id <run-id>   # the model alone, no library
nix develop -c python3 eval/human.py make --run <run-id>  # then fill in the sheet and run `score`
nix develop -c python3 eval/import.py --answers <file> --queries <files> --run-id <id> --system "<what>" --model "<name>"
nix develop -c python3 eval/report.py --run <run-id> --systems <run>,<run>   # adds a table of runs on their shared questions
```

## Release check

A release candidate answers every gated file in one run. Then it is compared with the reference run of the last release:

```sh
Q=eval/test.jsonl,eval/canary.jsonl,eval/abstention.jsonl,eval/robust.jsonl,eval/recent.jsonl
nix develop -c python3 eval/run.py --queries $Q --phone --run-id <candidate>
nix develop -c python3 eval/abstain.py --run <candidate>
nix develop -c python3 eval/pairwise.py --a <candidate> --b <reference>
nix develop -c python3 eval/report.py --run <candidate> --gate <reference>   # exit 1 on fail
```

The gate fails on any error, any failed canary, an Unverified-sentence rate more than 1 point above the
reference, fewer `expect` passes, more than one lost abstention, or a significant pairwise loss
(more losses than wins, sign test p < 0.05) on the locked test split. The report puts the gate table
above the results. The citation columns (Unverified, uncited, sources cited) need no LLM; they come
from the answer segments that the core's citation check writes. The report also gives a bootstrap 95%
interval for each score ratio, the judge points per rubric item ("Where the points go"), and "Answers to
look at": failed checks, then the 10 lowest scores with the judge's reason and, after `evidence.py`, whether
retrieval or the generator failed. With `--gate`, a table lists what changed against the reference:
flipped checks, flipped abstention verdicts and pairwise losses. A "Summary" block at the top of the
results computes the main points: the largest rubric gap, the weakest category, the retrieval and
generator failure counts, misspelled against clean wording, answer length, judge order agreement and
the grading cost.

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
