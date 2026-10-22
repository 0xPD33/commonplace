# Benchmarks

This file holds the measurements that describe the current app: speed and memory on the phone, answer quality, and the experiments behind the design. Each measurement names its device, build and date. The acceptance device is a Pixel 10 on GrapheneOS. Desktop runs measure quality and check code paths; they say nothing about phone speed. Measurements of models and builds that the app no longer uses are in the git history of this file (`git log -p docs/BENCHMARKS.md`).

## Summary

Pixel 10, Ling-3.0-tiny Q4_0, the distilled Ettin-17m reranker (`model_sel8.onnx`) on 24 passages, the question rewrite off.

| Measure | Value | Build, date |
|---|---|---|
| Card (search, rerank, best passage) | Median 0.54 s with the Starter set, 0.60 s with the Reference shelf | 0.2.3 release, 2026-10-21 |
| First word of the answer | Median 6.3 s (range 4.4–13.0 s) | 0.2.3 release, 2026-10-21 |
| First word, in-app benchmark (10 questions) | Median 7.2 s (5.9–13.4 s) | 0.2.0, 2026-10-13 |
| Decode | 16–17 tok/s on a cool phone; 8–9 tok/s after the phone passes about 37 °C | 0.2.0, 2026-10-13 |
| Model start after the app was closed | 16.3–16.6 s with the saved warm-up state, 23.9–26.0 s without it | 0.2.0, 2026-10-13 |
| Peak RSS with Ling | 5.3 GB (limit: 6 GiB with the app on screen) | 0.2.0, 2026-10-13 |
| Peak PSS with MiniCPM5-2B (8 GB phones) | 2.55 GB | `c2717c0`, 2026-10-19 |
| Voice input | +0.58 GB PSS while the mic is on | 2026-09-29 |
| Seeds, score ratio against Claude with web search | 0.533 (63 questions, Starter set, judge noise ±0.05) | `v22-answer-rules`, 2026-10-22 |
| Seeds with the Reference shelf | 0.555 (63 questions, Starter set and Reference shelf) | `v23-starter-shelf`, 0.2.4, 2026-10-23 |
| Conversation threads, score ratio | 0.649 on 92 tuning threads, 0.646 on 30 locked test threads (Starter set) | `cv3-dev-024`, `cv3-test-024`, 0.2.4, 2026-10-23 |
| NQ-open retrieval, 2,610 held-out questions | hit@5 0.738, hit@10 0.780, pool recall 0.825 | 2026 Wikipedia, 2026-10-02 |

Acceptance limits: card ≤ 1.5 s and first word ≤ 10 s at the median. Both pass.

## Devices and methods

**Pixel 10:** Tensor G5, 11.5 GB MemTotal, 128 GB UFS 3.1, PowerVR DXT-48-1536 GPU, GrapheneOS on Android 17. The kernel reports 2 small cores (capacity 207), 5 medium (824) and 1 big (1024). Android 17 limits an app's cgroup to 6 GiB of `memory.high` while it is on screen and to 3 GiB when it is hidden. Above the limit, the kernel swaps the model out, and decode falls to about 5 tok/s.

**Desktop:** AMD Ryzen 9 9950X3D, 62 GB RAM, RTX 5060 Ti and RTX 5080 laptop GPU for training and embeddings. `run.py --phone` gives the desktop the phone's settings: `--fuse-keep=24`, the int8 leaf-mt encoder, the phone's reranker file and the Ling pack.

**How the phone numbers are taken:** the app's Diagnostics screen and `files/telemetry.jsonl` give the card, prefill, decode and first-word times. `llama-bench` runs pinned to the big cores (`-C 0xFC --cpu-strict 1`), with repacked weights loaded into RAM (`-lm none`) and 3 repetitions. Heat changes every phone number, so each run gives its thermal headroom or temperature.

**How quality is measured** ([EVAL](EVAL.md), [eval/README](../eval/README.md)):

- 63 seed questions in 9 categories. Claude Opus answers them with web search as the baseline, and Claude judges both answers. The score ratio is ours ÷ baseline. One run's noise is about ±0.05.
- Blind pairwise judging of two of our runs on the same questions, for changes that are smaller than the noise of the ratio.
- Conversation threads (`eval/conversation.jsonl`): 92 tuning threads and 30 locked test threads.
- NQ-open: 2,610 held-out validation questions. A question is a hit at k when an answer string occurs in one of the top k passages.

## Phone: speed and memory

### Clean install of the 0.2.3 release (2026-10-20 to 2026-10-21)

The signed 0.2.3 APK (`4d29fbe`) on a phone with no earlier install. Paddy followed README "Install on a phone" by hand. The phone was on USB power and in airplane mode.

| | Starter set | Starter set and Reference shelf |
|---|---|---|
| Questions with a model call | 10 | 11 |
| Card, median (range) | 542 ms (412–934) | 599 ms (430–843) |
| First word, median (range) | 6.3 s (5.0–13.0) | 6.3 s (4.4–9.1) |
| Decode | 12.3–15.5 tok/s | 7.5–15.9 tok/s |
| RSS | 4.9 GB | 5.0–5.1 GB |

- The browser download of `commonplace-starter.tar` (11.3 GB) took about 3 h on 2.4 GHz Wi-Fi at about 1 MB/s. Install from Downloads of the Reference shelf (2.0 GB) took less than 1 minute.
- "Ready" came 18 s after a force-stop.
- With the Starter set, the squeaky hinge and the tap water in Mexico City got "the sources do not contain…". With the Reference shelf, travel questions (tipping in Japan, tap water in Mexico City, vaccinations for Kenya, a jellyfish sting) got cited answers from Wikivoyage and CDC travel.
- Thinking mode: a density comparison took about 75 s to the full answer on a hot phone (battery 40 °C).

### The 0.2.4 release (2026-10-22)

The signed 0.2.4 APK (working tree of `ccaa6cf`), installed over 0.2.3. The install kept the library. Tap to last word: "How do vaccines work?" 19.2 s, its two follow-ups 22.3 s and 21.5 s, "are you sure?" 16.3 s, "How do I fix a squeaky door hinge?" 21.6 s, "Who was the US president in 2016?" 13.4 s. The test found two faults, and the experiments table lists both fixes.

### In-app benchmark and model start (0.2.0, 2026-10-13)

The 0.2.0 debug build (`c552cf4`), the Starter set and the breadth packs, on USB power. The script waited for a skin temperature of 36.4 °C or less before each run. Each question restored 1,096 cached prompt tokens and prefilled 490–712 new ones.

| Measure | Run 1 | Run 2 |
|---|---|---|
| Card p50 | 407 ms | 533 ms |
| First word p50 | 7.0 s | 7.4 s |
| Decode p50 | 13.4 tok/s | 11.3 tok/s |
| Rerank of 24 passages | 270–362 ms | 263–605 ms |
| Decode, questions 1–2 / question 5 | 16.6 / 8.4 tok/s | 16.4–17.0 / 8.1 tok/s |
| Thermal headroom, first / last question | 0.71 / 0.86 | 0.73 / 0.87 |
| Peak RSS (VmHWM) | 5,315 MB | 5,294 MB |

Time from launch to "Ready": 26.0, 23.9 and 23.9 s without the saved warm-up state, and 16.6, 16.6 and 16.3 s with it. The state file is 27.8 MB. The rest of the start is the load of the 4.9 GB model into RAM.

### Long sessions and heat

Three conversations of 25 questions each (follow-ups, topic switches, typos, false premises, units, recent facts), typed through the app's UI with no pause, on USB power. The 0.2.0 debug build (`be6d684`), 2026-10-15:

| Questions | First word, median | Decode | Tap to full answer, median |
|---|---|---|---|
| 1–10 | 13 s | 8.7 tok/s | 34 s |
| 31–40 | 19 s | 5.2 tok/s | 57 s |
| 61–75 | 28–30 s | 4.8 tok/s | 63–65 s |

- 75 of 75 answered, with no crash. PSS rose from 5.33 to 5.50 GB. Prefill fell from 60 to 25 tok/s over the run.
- Later builds read 400 evidence tokens on a hot phone and run no planner. Session s2 of the same test, typed the same way (25 questions; 29 on 2026-10-19):

| | 0.2.0 (`be6d684`) | 0.2.1 | `2bba986` | `c2717c0`, Ling | `c2717c0`, MiniCPM5-2B |
|---|---|---|---|---|---|
| Crashes | 0 | 0 | 0 | 0 | 0 |
| First word, median | 21 s | 29 s | 18 s | 14.7 s | 11.7 s |
| Tap to full answer, median | 55 s | 67 s | 41 s | 40.3 s | 42.6 s |
| PSS at the end | 5.41 GB | 5.50 GB | 5.45 GB | 5.40 GB | 2.55 GB |

Session s2 was the second session in the first runs, so those runs started on a hotter phone. RSS grows by about 9 MB per question: RssAnon stays flat, and RssFile grows with pages of the memory-mapped indexes, which the kernel can reclaim.

### `llama-bench`

Pinned to the cores of the mask, repacked, in RAM, 3 repetitions, on USB power.

| Model | Mask | Threads | Prefill pp512 | Decode tg64 | Date |
|---|---|---|---|---|---|
| Ling-3.0-tiny Q4_0 | 0xFC | 4 | 95.0 ± 21.6 tok/s | 23.1 tok/s | 2026-09-29 |
| Ling-3.0-tiny Q4_0 | 0xFC | 6 | 131.6 tok/s | 25.5 tok/s | 2026-09-29 |
| Ling-3.0-tiny Q4_0 | 0xFC | 6 | 122.7 ± 16.6 tok/s | 26.7 tok/s | 2026-10-19 |
| MiniCPM5-2B, pure Q4_0 | 0xFC | 4 | 137.4 tok/s | 19.9 tok/s | 2026-10-19 |
| MiniCPM5-2B, pure Q4_0 | 0xFC | 6 | 190.6 tok/s | 21.0 tok/s | 2026-10-19 |
| MiniCPM5-2B, pure Q4_0 | 0xF8 | 5 | 166.0 tok/s | 21.3 tok/s | 2026-10-19 |
| MiniCPM5-2B, mixed Q4_0 (bartowski) | 0xFC | 6 | 166.6 tok/s | 19.5 tok/s | 2026-10-19 |
| LFM2.5-1.2B Q4_0 | 0xFC | 4 | 286.1 tok/s | 37.4 tok/s | 2026-10-19 |

- The pure MiniCPM file (all 296 matrices Q4_0) prefills 12–15% faster and decodes 8–9% faster than the mixed file, because llama.cpp repacks only Q4_0 tensors for ARM.
- Ling decodes fastest at 6 threads. MiniCPM gains little after 4 threads.
- In the app, prefill runs at about 92 tok/s on a cool phone, against 137–190 tok/s in `llama-bench`: the bench pins its threads and the app does not.
- Weights must be repacked: LFM2.5-8B-A1B ran at 51 tok/s prefill with plain weights against 120 tok/s repacked (2026-09-29). mmap plus repacking peaked at 7.0 GB of RAM, so the app reads the model into RAM (`LLAMA_LOAD_MODE_NONE`).

### Reranker on the phone

| File | 40-passage rerank on the Pixel | Date |
|---|---|---|
| `model_qint8_arm64.onnx` (shipped until 2026-10-02) | 509 ms (cool) to 787 ms (hot) | 2026-10-02 |
| `model_nb8.onnx` | 1,185 ms | 2026-10-02 |
| `model.onnx` (fp32) | 1,410 ms | 2026-10-02 |
| `model_nb8.onnx` on 24 passages, in-app benchmark | median 424 ms (392–851) | 2026-10-02 |
| `model_sel8.onnx` (distilled, the current file) on 24 passages, in-app benchmark | median 297 ms (263–605) | 0.2.0, 2026-10-13 |

On the desktop, sel8 reranks 24 passages in a median of 131 ms against 180 ms for nb8.

### Small parts

- Featured snippet: the int8 DeBERTa-v3-xsmall reader on the top 3 passages takes 234 ms on the Pixel (2026-09-29).
- Wikidata second hop: the multi-hop seeds "1964 Summer Olympics → Japan → Mount Fuji" and "Angkor Wat → Cambodia → riel" gave cards of 663 ms and 475 ms and first words at 11.2 s and 10.5 s (`be6d684`, 2026-10-14).
- Voice input: app PSS 5.12 GB before the mic tap, 5.70 GB with the mic on, 5.16 GB after the stop tap (2026-09-29).
- Thinking mode: 384 reasoning tokens took 34 s at 14 tok/s (2026-09-29). The default cap is now 256 tokens, about 18 s.

### LiteRT-LM: GPU and Tensor NPU (2026-10-22)

LiteRT-LM 0.18.1, Gemma 4 E2B (`litert-community/gemma-4-E2B-it-litert-lm` @ `b3ca0d2`), a debug build with its own package name, two questions, thermal headroom 0.78. Gemma on LiteRT's CPU backend (2026-09-29) is the reference row.

| File, backend | First word | Prefill | Decode | RSS | Result |
|---|---|---|---|---|---|
| Generic file, CPU (0.17.1, 2026-09-29) | 4.5–5.6 s | about 170–200 tok/s (~800 tokens in 4.0–4.8 s) | 9.5–10.5 tok/s | 2.8–2.9 GB | Works |
| Generic file, GPU (OpenCL) | 3.1–3.6 s | 516–613 tok/s | 9.5–9.6 tok/s | 2.1 GB | Correct, cited answers |
| `-gpu.litertlm`, GPU | — | — | — | — | Random tokens in many scripts |
| `_Google_Tensor_G5.litertlm`, NPU | — | — | — | — | SIGSEGV in the LiteRT v2.2.0 dispatch library |

- The app estimates LiteRT's prompt tokens from the characters (÷ 4). The first GPU load took about 50 s, while LiteRT compiled the OpenCL kernels.
- The Pixel exposes `libOpenCL.so` and `libedgetpu_litert.so` to apps as vendor public libraries, with no Play Services.
- NPU: LiteRT-LM 0.17.1 rejects the v2.1.6 dispatch library ("Unsupported dispatch runtime version"). Both 0.17.1 and 0.18.1 crash inside the v2.2.0 one. No newer prebuilt library exists; LiteRT-LM 0.18.1 pins LiteRT `ca21dc99`, and a dispatch library built from that commit with Bazel is the only path left.
- llama.cpp on the PowerVR GPU was never run here. Planning-stage numbers (about 24 tok/s prefill, 2.5 tok/s decode, no integer dot product in the driver) ruled it out.

The app keeps Ling on the CPU, and the TPU engine choice is off (`TPU_AVAILABLE`): Gemma's answer quality is not measured, and it would be an extra 2.5 GB pack.

## Answer quality

### Seeds over time

63 seed questions, Ling-3.0-tiny, desktop. Every run is a row in `eval/history.csv`, and the cited runs are in `eval/runs/`.

| Run | Library and settings | Change | Score ratio |
|---|---|---|---|
| `v9-rewrite` | 2023 Wikipedia, all packs | first judged run (2026-09-29) | 0.446 |
| `v16-enwiki2026` | 2026 Wikipedia, all packs | the 2026-09-01 Wikipedia dump | 0.508 |
| `v17-norewrite` | same | question rewrite off | 0.521 |
| `v18-release` | same | clean passage text, better entity linking (2026-10-05) | 0.529 |
| `master`, `cp-m3` (not in the repository) | phone settings, Starter set | before and after the merged build of 2026-10-10 | 0.48, 0.50 |
| `v19-phone-starter` | phone settings, Starter set | the 0.2.0 build (2026-10-14) | 0.530 |
| `v21-link-fix` | same | the "Should I" link fix (2026-10-22) | 0.546 |
| `v22-answer-rules` | same | general knowledge, underline check, check turns (2026-10-22) | 0.533 |
| `v23-starter-shelf` | phone settings, Starter set and Reference shelf | the 0.2.4 engine (2026-10-23) | 0.555 |

- The 2026 Wikipedia is the largest single gain. Out-of-corpus questions rose from 0.33 to 0.65, explanation from 0.41 to 0.52 and comparison from 0.37 to 0.45.
- Since `v18`, every change sits inside the judge noise of ±0.05. Pairwise tests in the experiments table carry the smaller effects.
- By category (`v22`, ours ÷ baseline): numeric 0.70, multi-hop 0.60, comparison 0.59, explanation 0.56, how-to 0.49, factual 0.42, travel 0.41, long tail 0.40, out of corpus 0.52. Evidence recall (the share of the baseline's sources that reached our evidence) is 0.13–0.54 by category, so retrieval limits most categories.
- The Reference shelf (`v23` against `v22`) raises travel from 0.41 to 0.53 and long tail from 0.40 to 0.56. Pairwise on all 63 seeds it is 27 / 9 / 27, with a mean gain of 0.52 points [−0.08, +1.13], so the overall gain is not significant. Travel gains 2.5 points, long tail 1.7 and comparison 0.9. Numeric loses 1.0 and explanation 0.3 (2026-10-23).
- With the older engine on 27 seeds, the full pack set beat the Starter set 17 / 2 / 6 pairwise (p = 0.035), and full evidence rose from 4 to 15 of 27 (2026-10-10).
- Model ceiling: Claude Sonnet, given exactly Ling's evidence on the 25 seeds with full evidence, scored 0.777 against Ling's 0.639 (2026-10-10). Across all 63 seeds that gap is worth about +0.05.

### Conversations

- The 0.2.4 engine with the phone's settings and only the Starter set (2026-10-23): 0.646 on the 60 original tuning threads and 0.646 on the 30 locked test threads. The runs of 2026-09-30 with the full 2023 library scored 0.616 and 0.614 on the same threads. All 92 tuning threads score 0.649. By kind: chat 0.97, topic switch 0.71, reformat 0.70, follow-up 0.60, expand 0.52, compare follow-up 0.49. Small talk ties the baseline easily, so without the chat threads the test ratio is 0.589 (0.556 on 2026-09-30).
- Score ratio on 60 conversation threads: 0.59 → 0.61 with the merged build of 2026-10-10; 22 topic-switch threads 0.59 → 0.70 (pairwise 17 / 1 / 4, p = 0.007).
- Evidence recall of the expected sources on 96 searched turns rose from 0.764 to 0.866 with the follow-up head (2026-10-11). Topic leaks into a new question fell from 17 of 22 to 0 of 22.
- The turn-kind head (question, expand, check, reformat, chat): cross-validation accuracy 0.964 with the fp32 encoder and 0.961 with int8. 89 of 92 conversation threads route right, and all 63 seeds stay questions (2026-10-22).

### Answers on the Pixel

The same 3 × 25 questions as in the long-session test, typed on the Pixel, each answer graded by Claude Sonnet with web search (2026-10-15):

| | Correct | Partly | Wrong | Unhelpful | Honest gap | Confidently wrong | Wrong topic |
|---|---|---|---|---|---|---|---|
| 0.2.0 (`be6d684`) | 27 | 29 | 11 | 5 | 3 | 18 | 3 |
| Follow-up topic fix | 37 | 24 | 11 | 2 | 1 | 17 | 1 |

The remaining confidently wrong answers are facts and numbers, not topics: Reagan for the president when the Berlin Wall fell, Trump for 2016, Ganymede's mass.

### Retrieval

NQ-open, 2,610 held-out questions:

| Order | hit@1 | hit@3 | hit@5 | hit@10 | Date |
|---|---|---|---|---|---|
| Fusion only (BM25 + dense + entity + prior) | 0.248 | 0.484 | 0.577 | 0.681 | 2026-09-30, 2023 library |
| Stock Ettin-17m reranker | 0.487 | 0.632 | 0.680 | 0.733 | 2026-09-30, 2023 library |
| Stock Ettin-17m, 2026 Wikipedia | — | — | 0.738 | 0.780 | 2026-10-02 |

The reranker files, on the distillation harness (161 questions with Claude-graded pools, and NQ; 2026-10-13). The rows compare with each other, not with the table above:

| Model and file | nDCG@8 all | nDCG@8 explain | NQ hit@1 | NQ hit@3 | NQ hit@10 |
|---|---|---|---|---|---|
| Stock 17m | 0.801 | 0.724 | 0.495 | 0.648 | 0.737 |
| Distilled 17m, `model.onnx` (fp32) | 0.808 | 0.727 | 0.515 | 0.654 | 0.738 |
| **Distilled 17m, `model_sel8.onnx` (the phone file)** | **0.813** | **0.734** | **0.510** | **0.653** | **0.737** |
| Ettin-400m (the teacher) | 0.885 | 0.829 | — | — | — |

- The distilled sel8 file gains +1.2 points of nDCG@8 over the stock model (95% interval +0.1 to +2.3). It closes about 15% of the gap to the teacher.
- Quantization: plain dynamic int8 loses about 6 points of NQ hit@1, because ModernBERT's activation outliers do not survive per-tensor quantization. `model_sel8.onnx` keeps layer 0's MLP output MatMul in fp32 and matches the fp32 quality (per-query Spearman against fp32: mean 0.992).
- A user document does not push Wikipedia out: with a 217-passage document that repeats Wikipedia text, NQ hit@1/5/10 stayed at 0.522 / 0.748 / 0.782 on 600 questions (2026-10-02).

### Voice input (desktop, 2026-09-29)

Moonshine Medium Streaming EN on a 44 s LibriVox clip, streamed in 100 ms chunks at real time: WER 2.5–4.2% over four runs, first live text 1.8 s after the audio starts, final text 0.33–0.46 s after a stop in the middle of a sentence, no text from 8 s of silence. Memory: 750 MB after load, 830–925 MB peak. A real voice on the phone's microphone is not tested.

## Experiments and decisions

Each row is a change that we tested. "Kept" means the app has it now.

| Date | Change | Test | Result | Decision |
|---|---|---|---|---|
| 09-29 | Ling-3.0-tiny against LFM2.5-8B-A1B | `llama-bench`, app on screen | Ling faster on prefill and decode, 5.1 vs 5.5 GB | Ling |
| 09-29 | Maple-Preview TQ1_0 (20B, ~1B active) | 63 seeds, `llama-bench` | 0.40 against Ling's 0.44; decodes 1.65× faster, prefills 21% slower | Not used |
| 09-29 | No LLM planner (routing in code) | 10 questions, desktop | First word 8.9 → 3.0 s; on the Pixel one planner call took 19–69 s | Kept; the planner is removed (10-15) |
| 10-02 | Question rewrite by the LLM | Pixel card, 63 seeds | Card 2.0 → 1.0 s without it; seeds 0.508 with, 0.521 without | Off by default |
| 10-02 | Rerank pool of 24 instead of 40 | 400 NQ questions | hit@5 0.710 against 0.713 | 24 |
| 09-30, 10-13 | Larger rerankers (Ettin-68m, cascade) | seeds, NQ | 68m: 0.486 at a card time of 3.9 s; cascade +1.0 to +2.4 points of hit@3 for +0.25 to +0.6 s | Not used |
| 10-10 | Faster retrieval (parallel dense, `probe_codes`, parallel decompress, sorted rerank batches) | 158 queries, desktop | Card 247–263 → 186–193 ms | Kept |
| 10-10 | BM25 query keeps only letters and digits | 273 NQ questions with `'` or `-` | BM25 pool recall 0.495 → 0.623 | Kept |
| 10-10 | Wikidata facts chosen by property vectors, not word lists | 77 questions | Asked property in the evidence for 43 of 47 (word lists: 35) | Kept |
| 10-10 | Neighbor passages, pack mix in the rerank pool | evidence labels, pairwise | No gain (pairwise 16 / 32 / 15) | Dropped |
| 10-11 | Wikidata second hop | 155 turns | 2 multi-hop seeds from partial to full evidence; +6 to 23 ms | Kept |
| 10-12 | Two worked examples in the answer prompt, question first | 63 seeds + 57 threads, pairwise | 33 / 11 / 19, +0.70 [+0.13, +1.26]; both sets p = 0.014 | Kept |
| 10-12 | Greedy decoding, DRY sampler | 63 seeds, pairwise | Greedy −0.04; DRY changed numbers ("1783" → "1781") | Dropped |
| 10-12 | Saved warm-up state | Pixel start | Start 24–26 s → 16.3–16.6 s | Kept |
| 10-15 | Follow-up topic from the first sentence of the referred answer | 120 held-out turns, Pixel sessions | Follow-ups that find the gold article 34/51 → 43/51; Pixel: correct 27 → 37 of 75 | Kept |
| 10-15 | 400 evidence tokens on a hot phone | 63 seeds, pairwise | 600 vs 400: 30 / 13 / 20, +0.17 [−0.40, +0.71]; 195 fewer prompt tokens | Kept above headroom 0.70 |
| 10-16 | Token estimate that counts each digit | 127 answers | Largest error 812 → 204 tokens | Kept |
| 10-19 | MiniCPM5-2B against LFM2.5-1.2B for 8 GB phones | 18 seeds | 0.494 against 0.374; pairwise 14 / 1 / 3 (p = 0.013) | MiniCPM5-2B |
| 10-19 | N-gram drafting on MiniCPM | 18 prompts | At most 14.6% of drafts accepted, no speed-up | Not used |
| 10-19 | Answer temperature 0.1 or 0 instead of 0.3 | 63 seeds | Ling 0.516 at both, 0.530 at 0.3 | 0.3 |
| 10-22 | General knowledge marked with [0] | 63 seeds | Ling never wrote the mark; 0.512 | Dropped |
| 10-22 | General knowledge without citations, with the underline check | 63 seeds, 92 threads | 0.547 / 0.533 against 0.546 (noise); threads 32 / 31 / 29 | Kept |
| 10-22 | Underline threshold 0.6 of the rarity weight | 123 sentences labeled by Claude | Underlines 49, 37 of them outside the evidence (precision 0.76) | Kept |
| 10-22 | Second signal for the underline (leaf-mt cosine, reranker score) | same 123 sentences | Wrong and right underlines score the same | Not used |
| 10-22 | Check turns ("are you sure?") | 10 threads, pairwise | 5 / 3 / 2, mean 6.45 against 4.50; the old head sent 5 of 10 to small talk | Kept |
| 10-22 | A check also searches the checked answer's first claim | 11 threads, pairwise | 7 / 2 / 2, mean 8.00 against 5.67 [+0.27, +3.82] | Kept |
| 10-22 | Rule against restating the earlier answer | 24 threads, pairwise | 9 / 6 / 9; the answers still restated the definition | Dropped |
| 10-22 | Rule to check dates against the question's year | 16 "who held this office then" questions | 12 of 16 right with and without it | Dropped |
| 10-22 | Swap the card to the first cited source | 62 seed answers | 19 do not cite source 1, but only about 3 of those cards are wrong | Not done |
| 10-22 | Gemma 4 E2B on the GPU or the Tensor NPU | Pixel, 2 questions | See LiteRT-LM above | Not used |

Known weak spots that these experiments did not fix:

- Questions about who held an office at a time: Ling answers "Donald Trump" for 2016 and "Vladimir Putin" for 2010 next to the correct term dates. Thinking mode fixed 1 of 4 such answers.
- Title traps: the reranker ranks the colour article "Sky blue" first for "Why is the sky blue?" (fp32 and int8 alike).

## Not measured yet

- The time of the underline check (rarity lookups) on the Pixel.
- The time of the Wikidata second hop on its own.
- Prefill with pinned threads and with `n_ubatch` 1024 against 512 in the app.
- Voice input with a real voice on the phone's microphone: transcript quality, stop-to-final latency, heat.
- Gemma 4 E2B answer quality on the phone.
