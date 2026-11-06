# eval

The eval harness. It measures our answers against Claude with web search. [docs/EVAL.md](../docs/EVAL.md) holds the current report.

## Files

| Path | What it holds |
|---|---|
| `queries.jsonl` | The 120 seed questions, in nine categories. |
| `dev.jsonl`, `test.jsonl` | The seed splits from `split.py`. `test.jsonl` is locked: run it to report, never to tune. `run.py` and `baseline.py` default to `dev.jsonl`. |
| `canary.jsonl` | 12 questions with an `expect` answer. Every one must pass on every backend. |
| `abstention.jsonl` | 15 questions that the library cannot answer and 15 with a false premise. `abstain.py` grades them. |
| `robust.jsonl` | 20 misspelled, spoken or misheard versions of seeds. |
| `recent.jsonl` | 20 questions on 2025 and 2026 events that the snapshot covers and the models do not know. This set measures what the library adds. |
| `practical.jsonl` | 20 "how do I" questions: household fixes, first aid, cooking. |
| `conversation.jsonl`, `conversation-test.jsonl` | 92 tuning threads and 30 locked threads: follow-ups, requests for more, topic switches, reformat requests and small talk. |
| `smoke.jsonl` | Three plumbing questions, not part of the eval. |
| `baselines/<id>.json` | One Claude baseline answer per query. |
| `runs/<run-id>/` | `meta.json`, `answers.jsonl`, `recall.jsonl`, `judgments.jsonl`, `pairwise-vs-<run>.jsonl`, `per_query.jsonl`, `stderr.log`. Git keeps only the runs that the docs cite. `EVAL_RUNS=<dir>` keeps the runs elsewhere. |
| `import.py` | Turns answers from any other system (an app, a model, a phone run) into a run. |
| `human.py` | A person checks the judge: `make` writes a sheet of 30 answers to mark, and `score` compares the marks with the judge. |

## Main runs

| Run | What it is | Score ratio |
|---|---|---|
| `v2-ref-ling` | The reference run and the source of `docs/EVAL.md`: Ling with the phone's settings, the Starter set and the Reference shelf, on test, canary, abstention, robust and recent (142 questions, 2026-11-02) | 0.64 |
| `v2-gemma-e2b` | The same with the Gemma 4 E2B Q4_0 GGUF, a desktop stand-in for the NPU file | 0.60 |
| `v2-closed-ling` | The same Ling with `--closed-book`: no library | 0.47 |
| `v2-ref-ling-r2`, `v2-ref-ling-rejudge` | The noise checks below, on the first 122 questions | 0.591, 0.592 |
| `v2-A-ref`, `v2-A-howto` | The reference set with the first how-to prompt and `wikibooks-en` without its alternative-medicine books. How-to rose from 4.18 to 4.90 of 10 over 34 questions | 0.65 |
| `v2-B-ref` | `v2-A-ref` plus `stackexchange`: how-to rose to 5.19, but other questions lost (12 better, 23 worse), so the pack stays optional | 0.62 |
| `v2-prac-ling`, `v2-prac-closed` | `practical.jsonl` with and without the library: 4.17 against 5.85 | – |
| `v3-ref-warm`, `v3-ref-warm2` | Two rolls of the 0.3 prompt at 400 evidence tokens, what a warm phone ran before 0.4.0: the reference of the 0.4.0 gate (142 questions, 2026-11-05) | – |
| `v4-rc-ling`, `v4-rc-ling2` | Two rolls of the 0.4.0 Ling candidate on the same questions | – |
| `cv3-dev-024`, `cv3-test-024` | The 92 tuning threads and the 30 locked threads, 0.2.4 engine, phone settings, Starter set (2026-10-23) | 0.649, 0.646 |
| `x-032-phone-cv` | The 30 locked threads in the 0.3.2 test app on the Pixel with Gemma (2026-10-24) | 0.605 |
| `v22-answer-rules` | 63 seeds, 0.2.4 changes, phone settings, Starter set (2026-10-22) | 0.533 |
| `v23-starter-shelf` | The same with the Reference shelf (2026-10-23) | 0.555 |
| `x-gemma4-phone2`, `x-gemma4-phone-shelf` | 63 seeds in the app on the Pixel with Gemma on the NPU, without and with the Reference shelf | 0.499, 0.511 |

[BENCHMARKS](../docs/BENCHMARKS.md) cites the other runs in `eval/runs/` next to the results that they support.

Runs before `v2-A-ref` used the general prompt for how-to questions; pass `--cli=--no-how-to-steps` to reproduce them.

## Noise

Measured on 2026-11-02 and 2026-11-05:

- On the desktop, the same build gives the same answers (`v2-ref-ling-r2` matches on all 122).
- The judge, run twice on the same 80 answers, moved the score ratio by 0.002 and 4 to 7 queries by a point or more.
- The bootstrap interval in the report, about ±0.05 on 80 questions, is the uncertainty that counts. It comes from the choice of questions.
- **Any prompt edit re-rolls about 80% of the answers**, even a no-meaning edit. A pairwise difference within about ±0.3, or a category result on 6 or 15 questions, is noise.
- `abstain.py` uses Opus: the Sonnet judge flipped 2 of 30 verdicts on identical answers, and the Opus judge none.

## Query schema

One JSON object per line:

```json
{"id": "cmp-07", "query": "…", "category": "comparison", "difficulty": 2,
 "time_sensitive": false, "expected_sources": [], "history": []}
```

- `category`: `factual`, `explanation`, `comparison`, `multihop`, `numeric`, `travel`, `howto`, `out_of_corpus` or `long_tail`. The conversation files use `followup`, `expand`, `compare_followup`, `topic_switch`, `reformat` and `chat`.
- `difficulty`: 1 to 3.
- `expected_sources`: titles, or `{"pack": "<id>", "title": "<title>"}`. When the list is empty, the gold set comes from the baseline.
- `history` (optional): earlier turns as `{"query", "answer"}`.
- `expect` (optional): a deterministic check. A list of strings passes when the first sentence contains one (case-insensitive). `{"text": [...], "sentences": 2}` searches the first two sentences. `{"number": x, "tol": 0.02}` passes when a number in the answer is within the relative tolerance; `number` can be a list.
- `behavior` and `note` (abstention set): what the answer must do, and the facts for the judge.
- `baseline` (robustness set): the id whose baseline the judge uses.

## Commands

Run each command from the repo root. Python exists only in the devshell.

```sh
nix develop -c python3 eval/run.py --queries eval/queries.jsonl --run-id <run-id>
nix develop -c python3 eval/baseline.py --queries eval/queries.jsonl     # run before recall.py and judge.py
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

A release candidate answers every gated file twice: once as built, and once with a no-meaning edit of each prompt that changed (the how-to prompt is separate), built in a worktree. Both rolls are compared with the two reference rolls of the last release:

```sh
Q=eval/test.jsonl,eval/canary.jsonl,eval/abstention.jsonl,eval/robust.jsonl,eval/recent.jsonl
nix develop -c python3 eval/run.py --queries $Q --phone --run-id <candidate>
nix develop -c python3 eval/run.py --queries $Q --phone --bin <edited build> --run-id <candidate2>
nix develop -c python3 eval/abstain.py --run <candidate>      # and <candidate2>
nix develop -c python3 eval/pairwise.py --a <candidate> --b <reference> --subset <test ids>
nix develop -c python3 eval/pairwise.py --a <candidate2> --b <reference2> --subset <test ids>
nix develop -c python3 eval/report.py --run <candidate> --roll2 <candidate2> --gate <reference>,<reference2>   # exit 1 on fail
```

The gate pools the rolls, except that the pairwise check takes one result per question, the mean of its rolls. It fails on any of these:

- an error, or a failed canary
- an Unverified-sentence rate more than 1 point above the reference
- fewer `expect` passes
- more than one lost abstention per roll
- a significant pairwise loss on the locked test split (more losses than wins, sign test p < 0.05)

Accuracy and speed tests are separate. The eval sets measure accuracy, and `scripts/bench/perf.jsonl` (9 demanding prompts, no judge) measures speed on a cool phone. `scripts/bench/phone_eval.py` runs a question set in the app on the Pixel, and `import.py` turns its answers into a run.

## Options

- `run.py --phone` runs what the phone ships: `--fuse-keep=24`, the quantized encoders and the GGUF of the library's own model pack. Choose the packs with `--library <dir>` and the build with `--bin`. `meta.json` records the flags, the binary with its sha256, and the model.
- `run.py` also takes `--model <gguf>`, `--think`, `--no-dense`, `--no-sparse`, `--no-rerank`, `--no-llm` and `--subset` (query ids or categories). `--cli=<flag>` passes a flag to `commonplace`, for example `--cli=--rewrite` or `--cli=--fusion=rrf`. `run.py`, `baseline.py` and `judge.py` resume where they stopped.
- `run.py --live-history` answers the earlier turns of a thread with the system first, as in the app. Reformat and small-talk threads keep the fixed turns, because their baselines use that text.
- `baseline.py` and `judge.py` use `claude --model opus` by default. `--budget-usd` caps the cost of each call.
- `recall.py` writes `recall@10` and `recall@40` (gold articles from any pack), `recall_wiki@10` and `@40` (Wikipedia gold only) and `recall@ev` (gold articles among the sources the model saw). `--library <dir>` searches another library, and `--out <file>` names the output.
- `pairwise.py` compares two of our runs. It uses the judge's rubric, swaps A and B for every query and hides the run ids. It prints win / tie / loss, a two-sided sign test and the mean difference with a bootstrap 95% interval. Identical answers count as ties and cost nothing.
- `evidence.py` asks Claude whether the evidence can answer the question (`full`, `partial` or `none`). `report.py` then splits lost queries into generator failures and retrieval failures.
- `report.py --history <csv>` writes the row to another CSV, and `--out <file>` writes the report elsewhere. The report gives a bootstrap interval for each score ratio, the judge points per rubric item and the worst answers. With `--gate`, it lists what changed against the reference.
