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
| Gemma 4 E2B on the Tensor NPU: first word, decode, full answer | 1.4–3.1 s, 27.0–30.8 tok/s with speculative decoding, 5.2–7.6 s | 0.3.0, 2026-10-23 |
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

The signed 0.2.3 APK (`4d29fbe`) on a phone with no earlier install. A person followed README "Install on a phone" by hand. The phone was on USB power and in airplane mode.

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

Gemma 4 E2B on the NPU (0.3.0 debug build, 2026-10-23): the phone eval runs `x-gemma4-phone` and `x-gemma4-phone2` asked the 63 seeds two times, 4.3 minutes apart. That is 126 questions in 76 minutes, each in a new conversation, on USB power. Thermal headroom rose from 0.57 to 0.83.

| Answers | Headroom | First word, median | Decode, median | Prefill, median |
|---|---|---|---|---|
| 11 seeds with the same evidence and answer in both runs | 0.63 | 1.86 s | 27.5 tok/s | 1,573 tok/s |
| The same 11 seeds | 0.78 | 1.83 s | 27.0 tok/s | 1,592 tok/s |
| 42 answers with the full evidence | 0.81–0.83 | 1.95 s | 24.9 tok/s | 1,506 tok/s |

- The paired seeds show no slowdown from 0.63 to 0.78. Above 0.80 the median decode is about 10 % lower, but those are other seeds, and speculative decoding is faster on some texts than on others.
- The slowest first word of the 126 answers was 2.76 s. In Ling's 75-question session, the first word went from 13 s to 28–30 s.
- The headroom never reached 0.85, where Ling's session stayed from question 10 on. The driver typed each question and opened a new conversation, so the NPU worked about 7 s of every 36 s. Ling's session answered with almost no pause. Gemma on a hot phone is not measured yet.

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

### LiteRT-LM: Gemma 4 on the GPU and the Tensor NPU (2026-10-22 to 2026-10-23)

LiteRT-LM 0.18.1 on the Pixel 10 with the Starter Wikipedia, in a debug build with its own package name. Each row asked "Why is the sky blue?" and "How do vaccines train the immune system?". The NPU rows also asked "What caused the fall of the Western Roman Empire?". Thermal headroom was 0.60–0.75.

| Model, file, accelerator | First word | Prefill | Decode | Full answer | Peak RSS | Result |
|---|---|---|---|---|---|---|
| Gemma 4 E2B, G5 file, NPU, speculative decoding | 1.4–3.1 s | 1,080–2,030 tok/s | 27.0–30.8 tok/s | 5.2–7.6 s | 2.2–2.3 GB | Correct, cited answers |
| Gemma 4 E2B, G5 file, NPU | 1.5–2.3 s | 795–2,080 tok/s | 18.6–19.0 tok/s | 7.9–8.9 s | 2.2–2.3 GB | Correct, cited answers |
| Gemma 4 E2B, generic file, GPU (OpenCL) | 3.1–4.8 s | about 420–610 tok/s | 9.5–9.6 tok/s | 16.6–20.3 s | 2.1 GB | Correct, cited answers |
| Gemma 4 E4B, generic file, GPU | 6.6–8.5 s | about 230–260 tok/s | 6.5–6.6 tok/s | 21.1–25.7 s | 3.1 GB | Correct, cited; one paragraph began with "COMPUTED:" |
| Gemma 4 E2B, `-gpu.litertlm`, GPU | — | — | — | — | — | Random tokens in many scripts |
| Gemma 4 E2B, G5 file, NPU, Google's v2.2.0 dispatch library | — | — | — | — | — | SIGSEGV in the dispatch library |
| Gemma 4 E2B, generic file, CPU (0.17.1, 2026-09-29) | 4.5–5.6 s | about 170–200 tok/s | 9.5–10.5 tok/s | — | 2.8–2.9 GB | Works |

- The NPU rows use LiteRT's own counter (`ExperimentalFlags.enableBenchmark`). The other rows estimate the prompt from its characters ÷ 4 and count one token per streamed message. On the NPU, that count matched LiteRT's counter (125 against 126 tokens).
- The NPU needs `libLiteRtDispatch_GoogleTensor.so` from the LiteRT commit that LiteRT-LM pins (`ca21dc99`). LiteRT-LM 0.17.1 rejects Google's v2.1.6 library ("Unsupported dispatch runtime version"), and 0.17.1 and 0.18.1 crash in the v2.2.0 library. Our build from `ca21dc99` works ([MODELS](MODELS.md#runtimes)).
- Speculative decoding (`ExperimentalFlags.enableSpeculativeDecoding`) is off in LiteRT-LM by default. The G5 file carries the draft model (a `verify` signature). It gave the same token counts and 1.5 times the decode speed. Its effect on the GPU and CPU is not measured.
- The first question after the app starts is the slowest on the NPU (first word 2.3–3.1 s).
- Gemma 4 E4B has no G5 file. The first GPU load of a model took about 50 s while LiteRT compiled the OpenCL kernels.
- Answer quality on the Pixel, with the 63 seeds asked in the app (0.3.0 debug build, the Starter set, 2026-10-23): a score ratio of 0.516 when 52 of the 63 answers had the warm-phone cut to 400 evidence tokens (`x-gemma4-phone`), and 0.499 with the full evidence (`x-gemma4-phone2`). Pairwise against Ling's desktop run `v22-answer-rules`, Ling won 35 / 7 / 15 (p = 0.007) with the cut, and 33 / 7 / 21 (p = 0.134, mean +0.53 [−0.17, +1.25]) with the full evidence. The full evidence against the cut: 23 / 17 / 17 for the full evidence, not significant. Numbers are Gemma's weakest point: Ling won all 5 numeric seeds, because Gemma often leaves out the COMPUTED value.
- The phone answers match the desktop stand-in: pairwise 25 / 13 / 19 against the Q4_0 GGUF run (`x-gemma4-e2b`, 0.523; pairwise against Ling 37 / 7 / 19). The phone run took 1.76 s to the first word and 6.0 s to the full answer at the median, and 1.94 s and 7.5 s with the full evidence.
- Tests that did not raise Gemma's quality (desktop, 63 seeds, pairwise): Google's QAT Q4_0 GGUF against the plain Q4_0 (0.505 against 0.523; 21 / 24 / 18, `x-gemma4-qat`); a rule to state the COMPUTED value (0.511; 23 / 24 / 16, numeric seeds 2 / 1 / 2, `x-gemma4-qat-prompt`); and about 1,000 evidence tokens instead of 600 (0.479; 26 / 16 / 21 with a lower mean, numeric seeds 0 / 2 / 3, `x-gemma4-qat-ev1200`). The search hands over at most 10 passages, so even a budget of 2,500 gave about 1,000 tokens; the NPU read the 2,151-token prompt without trouble. An answer prompt in the style of Google's Gemma 4 E2B RAG sample did not help either (2026-10-23). It had no "ONLY the numbered sources", and the fallback rule also stood next to the question. It scored 0.516 against 0.526, pairwise 27 / 14 / 22 (p = 0.57, `x-gemma4-d-prompt` against `x-gemma4-d-base`). It won in comparison, travel and how-to, and lost in factual questions. A target of 150 to 250 words instead of 100 to 200 did not help either (2026-10-24): the median answer grew only from 86 to 96 words, the score was 0.527 against 0.526, and pairwise 19 / 28 / 16 (p = 0.74, `x-gemma4-d-len`).
- Numbers on the Pixel (debug build, 2026-10-23): a speed in the question ("walking 5 km an hour") and an entity's length now give a COMPUTED time. The engine removes a copied COMPUTED label and LaTeX math from the answer, and decimals get thousands separators. On the 13 numeric and comparison seeds, pairwise against `x-gemma4-phone2`: 2 / 9 / 1 for the change, mean 8.6 against 7.6, p = 1.0 (`x-gemma4-phone-num`; the driver did not ask one comparison seed). The Great Wall seed rose from 2.0 to 9.5: before, Gemma took Wikidata's walking speed of 0.9 m/s instead of 5 km/h. The loss (Netherlands density) left out the 2.05 ratio. On the desktop GGUF, the change gave 0 / 11 / 2 (`x-gemma4-num-fix` against `x-gemma4-num-base`), because that model did not make the phone model's errors.
- The Reference shelf on the Pixel (debug build with the number changes, 2026-10-23): 0.511 against 0.499 with the Starter set alone (`x-gemma4-phone-shelf` against `x-gemma4-phone2`). Pairwise 28 / 14 / 19 for the shelf, mean +0.57 [−0.17, +1.32], p = 0.24. Travel rose from 0.41 to 0.53 (7 / 0 / 1), the same gain as Ling's. How-to won 5 / 0 / 2, but its ratio stayed at 0.41. Explanation lost 3 / 2 / 6, and factual fell from 0.47 to 0.41. Numeric rose from 0.57 to 0.75, but this run also has the number changes.
- Conversations, on the desktop with the same GGUF (`x-gemma4-cv-test`, 2026-10-23): 0.640 on the 30 locked test threads against Ling's 0.646, and 0.597 against 0.589 without the small-talk threads.
- The 0.3.2 test app on the Pixel with the Starter set, the Reference shelf and Gemma (2026-10-24): the 63 seeds gave the same answers, word for word, as `x-gemma4-phone-shelf` on 0.3.0 (`x-032-phone-full`). The answers were new, with other timestamps and times. With the same evidence, Gemma on the NPU writes the same words, so 0.3.1 and 0.3.2 did not change the seed answers.
- Conversations on the Pixel (0.3.2 test app, the same packs, 2026-10-24): 0.605 on the 30 locked test threads and 0.555 without the small-talk threads (`x-032-phone-cv`). The app wrote each earlier turn itself; the desktop runs use fixed earlier turns. Pairwise against `x-gemma4-cv-test` it is 10 / 6 / 10, mean 7.65 against 7.67 (26 threads). First word 1.96 s and full answer 6.53 s (medians).
- On the Pixel with 0.3.0: a six-turn conversation (follow-ups, "are you sure?", "make it shorter", "thanks") took 9.7–13.8 s per turn. A reformat turn showed its first word after 0.34 s and wrote 34.6 tok/s. With Think on and a budget of 256 tokens, the first word of the answer came after 16.3 s ("Thought for 14 s").
- The import of `gemma4-e2b-npu.tar` (3.1 GB) from Downloads took 28 s.
- Model start with the 0.3.2 release (2026-10-24): "Ready" came 9.2 s and 6.1 s after a force-stop and a new start. The test read the screen about once a second.
- From 0.3.0, the app uses Gemma on the NPU when the Gemma pack is installed. Settings can choose Ling.
- llama.cpp on the PowerVR GPU was never run here. Planning-stage numbers (about 24 tok/s prefill, 2.5 tok/s decode, no integer dot product in the driver) ruled it out.

### Gemma prefill during the search (2026-11-02)

Pixel 10, `app.commonplace.spike` debug builds of the branch `spike/npu-overlap` on `ac3829e` (uncommitted changes), enwiki-core, wikidata-facts and `gemma4-e2b-npu`, speculative decoding on. 17 seed questions, each as a new topic, plus one warm-up question per round. The builds ran in the order A, B, C, A, B, C, each after `cmd package compile -m speed-profile -f` and 90 s. Thermal headroom was 0.5–0.8. The table gives the medians of 34 questions per build.

| Build | Card | Rerank | Reader | Prefill after the card | First word | Decode |
|---|---|---|---|---|---|---|
| C: `Conversation`, as in 0.4.0 | 501 ms | 384 ms | 188 ms | 1,181 ms | 1,865 ms | 27.6 tok/s |
| A: `Session`, system prompt and question prefilled during the search | 534 ms | 391 ms | 209 ms | 539 ms | 1,277 ms | 27.8 tok/s |
| B: as A, 6 encoder threads instead of 4 | 629 ms | 485 ms | 312 ms | 549 ms | 1,459 ms | 27.8 tok/s |

- A gave the first word sooner on all 17 questions (paired medians): by 566 ms at the median, 54 to 793 ms.
- The prefill of the system prompt and the question took 650 ms at the median, more than the card. So the first word still waits about 0.1 s for it.
- Answer length per question did not change (paired median −1 token). No answer showed template text or hit the token limit. Answer quality is not judged yet.
- Six encoder threads made the rerank 95 ms and the reader 120 ms slower: the sixth thread probably runs on a small core.
- On the first question after the model loads, the same prefill took 1.9 s.
- The driver typed a backslash before each question mark ("2016\?"), in all three builds alike.
### CPU path: build flag, MoE prefill and energy (2026-11-02 to 2026-11-03)

Pixel 10, `llama-bench` and `llama-completion` from llama.cpp b12032 built with the app's flags, branch
`spike/cpu-speed` on `ac3829e` (uncommitted), `-C 0xFC --cpu-strict 1`, repacked weights in RAM (`-lm none`).
Ling is the shipped Q4_0 file; MiniCPM5-2B is a pure Q4_0 requantized from the official Q8_0 for these tests.

`+fp16` in `GGML_CPU_ARM_ARCH` (ABBA order, two runs per build, skin 40-42 °C, 4 threads decode, 6 threads prefill):

| Model | Build | Decode d0 | Decode d1100 | Prefill pp512 d0 | Prefill d1100 |
|---|---|---|---|---|---|
| Ling | app flags | 17.6 | 12.2 | 113.4 | 64.9 |
| Ling | +fp16 | 15.6 | 16.1 | 106.2 | 61.9 |
| MiniCPM | app flags | 11.7 | 8.0 | 99.7 | 59.8 |
| MiniCPM | +fp16 | 11.6 | 10.7 | 98.8 | 58.4 |

- Decode at 1,100 tokens of context, where the app always runs: MiniCPM +34%; Ling +32% is unreliable (its row
  shows depth 1100 faster than depth 0, so heat noise dominates). Prefill does not change. At depth 0,
  Ling decode measured 17.6 -> 15.6 tok/s (-11%), but its first app-flag run was the coolest of the set; MiniCPM did not change.
- MiniCPM perplexity at 2,048 context is identical (10.1890, 3 chunks). Greedy answers to two RAG prompts: one
  identical (94 words), one diverges after 22 words into another cited answer. The eval decides.
- On the desktop, decode loses only 7% at depth 1,100 (Ling 61.3 against 57.2 tok/s); the phone ran 24-25% slower
  without `+fp16`. Runs of one Ling build varied by up to 30% in this set, so the MiniCPM gain (+34%) is the firm one;
  the prefill figures came from a hot phone (113 tok/s here against 167 for the same stock build in a cooler run).

MoE expert matmuls as GEMM (`patches/llama.cpp/0001-moe-gemm.patch`), Ling, 6 threads: prefill 167 -> 201 tok/s at
depth 0 and 88 -> 115 tok/s at depth 1,100. Decode and perplexity do not change (9.2871 on 8 chunks, desktop).

Energy of Ling decode at depth 1,100, from a trace of the battery's current and voltage: 0.32-0.35 J per token for
every thread setting tried (masks 0xFC and 0x7C, poll 50 and 0, 3 and 4 threads). Fewer cores lower the power and
the speed together. Today's setting is the fastest; it fell from 18.1 to 13.1 tok/s in 7 minutes of nonstop decode.

Speculative decoding with the MiniCPM5 DSpark drafter (Q4_0): 2.2-2.6 tokens per step on the Pixel. The best runs
match plain decode (19 against 17.5-20 tok/s): per step the drafter takes about 40 ms and the 4-token verify about
75 ms. These runs had thread-pool contention (two pools on shared cores, see "Speculative decoding with a draft
model on shared cores" below), so they do not count; 0002 now makes the draft context share the target's pools. The medium cores are two clock domains (cpus 2-4 and 5-6): a pool pinned across them waited for cpus
5-6 at 921 MHz while cpus 2-4 ran at 2.9 GHz.

Speculative decoding acceptance, desktop (acceptance does not depend on the hardware): MiniCPM5-2B pure Q4_0, 10 real
answer prompts (system prompt, evidence, question; about 1,700 tokens), 200 tokens each, top_k 40, top_p 0.9, temp 0.3.

| Drafting | Tokens per step | Drafted per step |
|---|---|---|
| DSpark Q4_0, draft length 2 / 3 / 4 / 5 / 7 | 2.11 / 2.39 / 2.52 / 2.68 / 2.76 | 2.0 / 3.0 / 4.0 / 5.0 / 7.0 |
| DSpark, length 5, p-min 0.4 | 2.48 | 2.77 |
| n-gram map-k 2-8 (from the prompt) | 1.50 | 2.58 |
| n-gram map-k 3-6 | 1.37 | 1.06 |
| n-gram simple (defaults) | 1.02 | 0.27 |
| n-gram map-k 3-6, then DSpark length 3 | 2.36 | 3.45 |

Patch identity on the desktop (x86; the arm64 kernels need the QEMU check): Ling, 120 seed questions through the CLI,
answers word for word identical with and without patches 0001-0004 (fixed sampler seed). KL divergence of patches
0001-0006 against stock: mean 1e-6, max 6e-5, same top token 100%, which equals stock against itself (the tool stores
the reference logits in 16 bits). Perplexity of a pure Q4_0 Ling requantized from the mixed file: 12.21 -> 12.69 (+4%,
16 chunks of 512 tokens of the docs); a proper requant from BF16 is not measured.

arm64 correctness of the llama.cpp patches, checked without the phone: aarch64 Linux builds (GCC 15, the app's
-march, KleidiAI on) under `qemu-aarch64 -cpu max`, one 256-token chunk, KL divergence against a stock build. With
0001-0004 and with 0001-0008 (the run also held the first 0009, which never ran): MiniCPM5 max KLD 2.9e-5, Ling max
KLD 3.7e-5, same top token 100% in every case, below stock against itself on x86 (max 6.3e-5). Phone speed of
0005-0010 is not measured yet.
Patch 0009 (Q5_0 through ARM tinyBLAS) needed a second version: ggml-cpu.c turns llamafile off on i8mm builds, so
the first version never ran. Under QEMU the working version matched the generic Q5_0 dot product within 1.2e-7 per
output, yet Ling's logits moved more: KL against stock mean 0.002, max 0.011, same top token 98.4%, perplexity
11.122 -> 11.085 (256 tokens). (An early guess blamed Ling's MoE routing; the dense-model test below refutes it.)
Against x86 logits (x86 runs Q5_0 natively) the series 0001-0010 had mean KL 0.0070 against 0.0073 for stock, but the
same top token 97.6% against 99.2% (one chunk, about +-1.4%). So 0009 changes Ling's answers, and its direction is
unknown: it needs the eval and the release gate before it ships, like any numerics change.
The MoE explanation is refuted: on a dense model (MiniCPM5 with ffn_down requantized to Q5_0, QEMU, 256 tokens)
0001-0010 against stock also gives KL 0.0053, same top token 92.9%. Against x86 logits both arm64 builds are equally
far (stock 0.0075, 0001-0010 0.0077; same top token 93.7% and 97.6%), so the change is not an accuracy loss. A code
review found no bug (layout, scales, quantized src1, thread split, type routing). Isolated: 0001-0010 without 0009
is at the floor against stock (max KLD 4.1e-5), so 0009 alone causes it. Tensor dump (llama-eval-callback, QEMU):
the first Q5_0 ffn_down output agrees in every printed digit and its sum differs by 3e-7 relative (24.576626 against
24.576633); the next residual sum differs by 2.6e-5, so per-element differences can be larger than the sum suggests.
Direct matmul check (scratchpad mmtest/mm.cpp, QEMU, Q5_0 6144 x 512 weights, against a double reference from the
same quantized inputs): with 0009 max relative error 2.6e-8 (13 tokens) and 1.22e-5 (64 tokens), without 0009
2.0e-8 and 1.22e-5. So 0009 adds no error beyond float rounding; the model-level KL is the model's sensitivity to
any rounding change (0012's correct change of Q rounding gives 0.0013 in the same kind of test). Against x86 logits
0009 is as close as stock. It needs the eval like any numerics change.

Prompt-lookup speculative decoding in `commonplace-llm` (`decode_ngram`, desktop, MiniCPM5 pure Q4_0, 30 seeds through
the CLI, temperature 0.3): 1.32 tokens per step with drafts from the user turn only; 1.52 with the cached system
prompt included and a draft of at least 2 tokens; 1.56 with a 3-token key that falls back to 2 (48% of drafts accepted,
against 43%); a cap of 4 drafts instead of 8 gave 1.48. Correctness by bisection (greedy, 10 seeds): the loop with no drafts
gives 10/10 answers identical to plain decoding, and plain logits plus decoded-and-dropped drafts give 10/10, so the
rollback is exact. With every draft rejected inside the batch, 4/10 stay identical: batched logits differ from
one-token logits at rounding level. The text therefore differs from plain decoding and needs the eval. Ling (hybrid
recurrent memory) is excluded. Phone speed: not measured yet.
Ling with 8 recurrent state snapshots (`n_rs_seq`, about +170 MB) so that `llama_memory_seq_rm` drops rejected drafts
inside a verify batch (desktop, 10 seeds, greedy): no rollback errors, 6/10 answers identical to plain decoding, the rest
diverge after 11-64 words; 1.33 tokens per step. The snapshots cover only the last batch, and its first token must stay.
Rollback check (test hook, every draft rejected, the same batch size per step in both runs, rollback depth 1-7): real
drafts against fixed filler tokens, 10/10 answers bit-identical over 1,054 steps and 4,174 rolled-back tokens, so the
right snapshot is restored; the gap to plain decoding is batch rounding. MiniCPM5 (attention KV only), whole batches
of up to 8 tokens rolled back and decoded again one at a time: 10/10 identical to plain decoding.
State files: the model key includes the snapshot count, so a context with snapshots never loads a state saved without.
In the app, prompt lookup is a separate preference (`prompt_lookup`, off, no UI, debug builds only).

Patch 0010 (MoE expert sum as one fused pass): fuses Ling's 7-ADD chains (8 terms) in every MoE layer; KL against
stock at the floor on x86 (max 4.8e-5, same top token 100%); desktop decode unchanged within noise (53.6-58.6 against
57.6-58.8 tok/s). Phone speed: not measured yet.

Verify-batch cost (desktop, Ryzen 9 9950X3D, 4 pinned threads, 0001-0010, 2026-11-03), time of a batch of n tokens
against one decoded token: MiniCPM5 depth 1100: 2 tokens 1.40x, 4 tokens 1.87x, 9 tokens 4.06x; Ling 1.39x / 1.94x /
3.50x; the same at depth 0. An earlier phone note gave 4 tokens at about 1.06x (pp4 64 tok/s against pp1 17 at depth 0, model not recorded;
the contended DSpark runs implied 1.3-1.5x), so the phone ratio is open until morning_verify.sh: the
desktop is not memory-bound at decode, so its ratios do not transfer. The phone figure at depth 1100 is pending.

Pending patch 0012 (small batches of 2-63 queries through tiled flash attention, heads that share K/V in one tile,
GEMMs on valid rows only, at least 16 rows per thread chunk). Desktop, 4 threads, depth 4000, ms per batch, before ->
after: MiniCPM5 2 tokens 55.8 -> 56.2, 4 tokens 86.1 -> 65.8, 8 tokens 167.3 -> 103.8; Ling 32.0 -> 32.3,
48.4 -> 49.2, 83.4 -> 67.9; decode and 512-token prefill unchanged. Earlier versions: one head per tile was 3-5x
slower (each 2-row tile paid the K/V conversion and full 64-row GEMMs); without the 16-row gate, Ling's 2-token
batch was 10% slower (8-row chunks). Numerics (MiniCPM5, x86, perplexity in 4- and 8-token ubatches against
512-token ubatches): with 0012 KL mean 0.000000, max 5.9e-5, same top token 100%; stock with 4-token ubatches mean
0.0013, 98.2%, because stock small batches round Q to FP16 in the per-row path. Ling: 0.004 either way (other
layers). Phone speed: pending (`morning_verify.sh`).
QEMU arm64 (KleidiAI on, 6 threads, MiniCPM5, 4-token against 256-token ubatches of the same build): 0001-0010
KL mean 0.00127, with 0012 (before the 16-row gate) 0.00126. On ARM the matmul kernels already differ between
small and large batches, so this run does not isolate attention. With KleidiAI off and `--no-repack` (matmuls give
the same numbers at every batch size), 4 threads: with 0012 KL mean 0.000000, max 3.1e-5, same top token 100%;
0001-0010 mean 0.0014, max 0.011, 97.6%. So 0012 is exact against the prefill on arm64 too.

Ling decode profile (desktop x86, perf, 4 threads, depth 0, 0001-0010): routed experts (Q4_0 repack gemv) 37%,
output head (Q6_K, 1536 x 157184) 21%, Q8_0 matmuls 14%, ggml_barrier 7.3%, get_rows + memmove + gated delta net
about 3%. Requantized Ling heads, from the shipped file (perplexity on the docs, 8 x 512 tokens, KL against the
shipped file): head Q6_K -> Q4_0 (189 -> 130 MiB): perplexity 13.552 -> 13.565, KL mean 0.0044, same top token
96.3%, desktop decode at depth 1100 +6-7% (57.4-59.3 -> 62.5-63.1 tok/s). Head plus every Q8_0 and Q6_K tensor ->
Q4_0: perplexity +1.6%, KL 0.029, same top token 91.6%; rejected. The head change needs the eval and the
maintainer's approval (it changes the model pack).

Decode graph size (x86 marker, one decoded token): Ling computes 1,578 nodes (3,164 in the graph), MiniCPM5 719;
Ling's extra nodes are tiny ops (ADD 135, RMS_NORM 115, SCALE 113, GET_ROWS 108, CPY 108, SIGMOID 83, ARGSORT 69,
CONCAT 66, ...), each followed by a barrier. Pending patch 0013 skips barriers: tiny nodes (at most 4,096 values)
run on thread 0 alone, chains of thread-0-only nodes need no barrier, and simple ops with disjoint memory need
none either. Barriers per token: Ling 1,577 -> 903, MiniCPM5 718 -> 716. KL against stock at the floor (Ling decode
at 3, 4 and 6 threads, 512-token prefill, MiniCPM5 decode). x86 decode: Ling -1.8% at depth 0, -0.9% at 1100 (an
x86 barrier costs about 0.7 us). Versions: independence only skipped 152 barriers; with thread-0 chains 333; the
tiny-node version first raced in Ling decode (KL 0.15, same top token 84%) because a node that starts a fused group
(the ADD chain, RMS_NORM+MUL) runs on all threads; the fix asks the fusion code before skipping. Phone: pending
(`morning_bar.sh`, after the decode profile). Barriers Ling keeps with 0013: 332 after matmuls, about 570 after larger
dependent ops (RMS_NORM, ADD, CONCAT, SCALE, CPY, CONT, GLU); none after the MoE routing ops, so fusing the routing
chain would not remove more.
0012 and 0013 together on 0001-0010 (x86, 4 threads): decode KL against 0001-0010 at the floor for Ling and
MiniCPM5; MiniCPM5 4-token ubatches against 512-token prefill: KL mean 0.000000, max 5.9e-5, same top token 100%.

Speculative decoding with a draft model on shared cores (desktop, 4 cores via taskset, -t 4, MiniCPM5-2B Q4_0 +
DSpark Q4_0, greedy, 128 tokens, acceptance 49% in every run; plain decode 46.3 tok/s): stock llama.cpp 7.4 tok/s;
0002 v1 (draft context with its own pools) 5.2; v1 with --poll 0 47.3; 0002 v2 (draft context shares the target's
pools) 47.2. Cause: the idle pool busy-waits (poll 50) on the cores the working model needs; in stock the target
pool spins while the draft's throwaway threads compute. On 32 cores without taskset the effect hides (42 tok/s).
The Pixel 10 spec runs had contention of this kind (separate pools on shared cores), so their DSpark numbers do
not count; the exact phone mechanism (unpinned short-lived draft threads) is not measured.
With the phone's flags on 3 strict desktop cores (plain decode 45.0 tok/s), 0002 v2: -t 3 -tb 3 37.7, -t 3 38.1,
-td 3 with the same -Cd 38.0 (pools shared), -td 2 16.7 (own pools, unpinned: the fallback still collides). -tb
makes the target's batch pool unpinned (no mask inherited), so the morning spec test no longer passes it. On the
desktop, which is not memory-bound, DSpark stays below plain decode; the phone decides.
Pending 0014 (a thread pauses its previous pool when it starts a graph on another; only live, idle pools): the -td 2
case 16.7 -> 37.4 tok/s, shared pools unchanged (37.5), same greedy text in all runs; stock llama.cpp 7.7 -> 10.9-12.7
(its throwaway draft threads remain; 0002 removes them).
Full stack (0001-0010 with 0002 v2, plus pending 0011-0014; they apply together): decode KL against 0001-0010 at the
floor for Ling and MiniCPM5 (x86); DSpark on the p00 setup 34.2 tok/s (0001-0004 + 0012: 34.7; 0013 costs a little
on x86).
DSpark with pending 0012 (desktop, 3 strict cores, one RAG prompt p00 of about 1,700 tokens, greedy, 0001-0004 +
0002 v2, three interleaved runs each, logs in the scratchpad's night/dsab/): per step draft 15.2 -> 14.5 ms, verify
81.2 -> 68.2 ms; decode 29.3-29.4 -> 34.7-34.8 tok/s; plain decode 32.8-33.1 tok/s. On this prompt 0012 turns DSpark
from 11% slower than plain into 5% faster. 0012 also changes the greedy text (133 -> 128 tokens), as any change to
verify numerics does. The phone test runs it as setup ds3-0012 on three prompts.
Draft length 5 with 0012 (same desktop setup, undisturbed runs): 26.8 tok/s against 35.0 at length 3; acceptance
45.6% against 63.6%, verify 102 ms against 68 ms per step. On the desktop length 3 is better; the phone test also
runs ds5-0012, because there a longer verify costs little more than one token.
DSpark acceptance at the app's sampling (temperature 0.3, top_p 0.9, top_k 40; desktop, RAG prompts p00/p04/p08,
150 tokens each; acceptance does not depend on the hardware): draft length 3: 50% of drafts accepted, about 2.5
tokens per step; length 5: 36%, about 2.8. Greedy gave about 2.9 at length 3, so the greedy morning test overstates
the app's gain by about 15%.

Can QEMU stand in for the phone in an eval of ARM-only numerics (+fp16, 0009, 0011)? Stock llama.cpp b12032 with the
phone's flags, MiniCPM5, greedy, RAG prompts p00 and p03, against the Pixel's output: a GCC 15 aarch64-linux build
under qemu-aarch64 -cpu max,sme=off,sve-default-vector-length=16 diverges at word 90 of 94 (p00) and at word 4 (p03,
a near-tie: "really created" against "created"); both texts are coherent. Plain -cpu max would pick KleidiAI's SME
kernels, which the phone lacks. A static NDK-clang build of the same source and flags (android toolchain,
-DCMAKE_EXE_LINKER_FLAGS=-static), under qemu-aarch64 -cpu max,sme=off,sve-default-vector-length=16 with the phone's
-t 4 -tb 6, reproduces the Pixel's output byte for byte on both prompts (94 and 136 words). So the GCC divergence
came from the compiler. A clang 21 aarch64-linux (glibc) build is byte-exact on both prompts too, so glibc's libm
does not matter here; GCC's float contraction does. Limits: two prompts only; the phone's model file was not
hashed (morning_qref.sh records its hash and 9 more reference answers); it covers stock llama-completion greedy, not
the app's engine (patches, sampling, thread pools), which needs its own static NDK build and a phone comparison
before QEMU can generate eval answers for ARM-only numerics changes. Speed: about 10 min per answer
(1,700-token prompt) per QEMU process; several processes run in parallel on the desktop.
Our engine's prompt lookup (desktop CLI, MiniCPM5, 10 RAG seeds twice, temperature 0.3, 4 cores): plain 33.9 tok/s,
lookup 29.3 without and 29.7 with 0012; on the desktop lookup loses either way (small batches cost about 1.4x a token
there); the phone decides.

Flash-attention tile constants (`ggml-cpu/common.h`, default Q 64 x KV 64), KL against stock on x86 (MiniCPM5):
Q 32 x KV 64 at the floor; KV 32 mean KLD 0.0012 (same top token 98.3%). An op-level check against a double reference
(scratchpad mmtest/fa.cpp: 64 queries, 4 heads over 2 KV heads, 64/100/1100/4000 keys, causal or not) gives max error
3-8e-7 for both KV 64 and KV 32, so KV 32 is correct and its KL is rounding sensitivity. Both Q 32 and KV 32 go to the
phone tile test.

Pending patch 0011 (KleidiAI leaves Q6_K to the repack buffer), QEMU arm64, Ling, 256 tokens, against x86 logits:
perplexity 11.122 stock / 11.085 with 0001-0010 / 10.936 with 0011 (x86 11.042); KL toward x86 0.0073 / 0.0070 /
0.0077; model load under QEMU 33.5 s -> 18.6 s. Not applied until phone speed, load time and the eval are in.

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

### Small models and their chat format (desktop, 2026-11-03)

`dev.jsonl` (60 questions), `--phone`, the full library, Opus judge. Build `ac3829e` (old format) against `ac3829e-dirty` on branch `spike/lfm3-prompt` (new format). The new format adds the BOS token when the chat template writes `bos_token` but the GGUF has `add_bos_token = false`. LFM3 and MiniCPM5 both had this gap, so their prompts lacked the BOS. It also sends LFM3's own thinking markers (`<think></think>`, no newlines) and LFM3's recommended sampling (temperature 0.1, top-k 50, repeat penalty 1.1, profile `lfm3`). Ling and Gemma prompts do not change. Times are desktop medians.

| Run | Model and format | Ratio | Median answer | First word | Full answer |
|---|---|---|---|---|---|
| `v2-dev-ling` | Ling | 0.646 | 408 chars | 2.26 s | 4.71 s |
| `x-minicpm-dev-bos` | MiniCPM5-2B pure Q4_0, with BOS | 0.621 | 310 chars | 2.50 s | 4.42 s |
| `x-minicpm-dev-old` | MiniCPM5-2B pure Q4_0, as shipped | 0.602 | 336 chars | 2.51 s | 4.59 s |
| `x-lfm3-dev-think` | LFM3-1.7B QAD Q4_0, new format, thinking on (budget 256) | 0.576 | 460 chars | 6.35 s | 8.68 s |
| `x-gemma-dev` | Gemma 4 E2B Q4_0 | 0.567 | 508 chars | 2.40 s | 5.71 s |
| `x-lfm3-dev-old` | LFM3, old format | 0.533 | 744 chars | 1.67 s | 5.89 s |
| `x-lfm3-dev-off` | LFM3, new format, thinking off | 0.524 | 716 chars | 1.67 s | 5.12 s |
| `x-spark-dev` | Spark-X2.6-1.7B Q4_0 (`bartowski/Spark-X2.6-1.7B-GGUF` @ `0e47f40e`, sha256 `744ff661…`), its own format, thinking off, our sampling | 0.478 | 1,311 chars | 2.03 s | 8.60 s |

Per question, by judge score (better / tie / worse):

- MiniCPM with BOS against as shipped: 27 / 14 / 17 by judge score, but `pairwise.py` gives 23 / 16 / 21 (p = 0.88, mean diff −0.13): no gain. The fix stays because it matches the model's own chat template.
- LFM3 new format against old, thinking off: 22 / 9 / 27, no gain.
- LFM3 thinking on against off: 29 / 13 / 18. It is still behind MiniCPM with BOS (18 / 11 / 31), and first words come 3.8 s later.
- LFM3's errors are mostly missing knowledge, for example "Donald Trump was the US president in 2016". The format did not cause them.
- Spark-X2.6 lost to MiniCPM with BOS 11 / 7 / 42 and to Gemma 11 / 9 / 40. Its answers are 4× MiniCPM's length (334 tokens) and often list the sources against the prompt's rule. llama.cpp does not know its chat template, so the branch adds Spark's format (`<|System|>`, `<|User|>`, `<|Bot|>`, a bare `</think>` for thinking off). Its card's sampling (temperature 1.0, top-p 0.95) and thinking mode are untested. Decode is 19.8 ms per token against MiniCPM's 25.4.

### Release 0.4.0 candidate (2026-11-03)

Branch `spike/040-candidate` on `ac3829e`: llama.cpp patches 0001-0010, 0012 and 0013 (`patches/llama.cpp/`), `+fp16` in the
arm64 build, Gemma's prefill during the search (`LlmBackend::prepare`) and the MiniCPM BOS fix. Desktop runs use x86, so they
do not exercise `+fp16` or the arm64 kernels.

- Desktop, Ling, dev split (`x-040-dev-ling` against `v2-dev-ling`): 43 of 60 answers identical, pairwise 6 / 44 / 10 (p = 0.45).
- Desktop, MiniCPM, dev split (`x-040-dev-minicpm` against `x-minicpm-dev-old`): pairwise 23 / 11 / 26 (p = 0.78, mean diff −0.21).
- Desktop, Ling, reference set (142 questions, `x-040-ref-ling` against today's master `x-master-ref-ling`): 122 identical,
  pairwise 8 / 124 / 10 (p = 0.82, mean diff +0.01). Unverified sentences 16.2% against 12.6% (6 more sentences in 5 how-to and
  abstention answers, after sampling diverged); expected answers 48 / 53 against 49 / 53.
- The release gate against `v2-A-ref` fails for master too (unverified 12.6% against 11.4%, limit +1%): the reference predates
  master's how-to changes and needs replacing.
- Pixel 10 `llama-bench` (`scripts/bench/ship_bench.sh`), stock against the candidate's llama.cpp, order A B B A per model, skin
  36-38 °C for all but the first run, tok/s:

| Model | Build | pp512 d0 | pp512 d1100 | tg64 d0 | tg64 d1100 |
|---|---|---|---|---|---|
| Ling | stock | 139.8, 155.2 | 70.3, 88.0 | 18.7, 24.5 | 12.1, 11.6 |
| Ling | 0.4.0 | 224.6, 225.3 | 163.3, 167.4 | 12.8, 24.8 | 17.8, 20.3 |
| MiniCPM | stock | 167.5, 123.3 | 110.7, 68.5 | 20.9, 12.9 | 13.8, 8.4 |
| MiniCPM | 0.4.0 | 173.5, 167.1 | 128.8, 111.8 | 11.2, 10.2 | 18.0, 11.1 |

  Ling prefill +50% (depth 0) to about 2× (depth 1,100), Ling decode at depth about +60%, MiniCPM decode at depth about +30%.
  MiniCPM decode at depth 0 was lower with 0.4.0 in both pairs; heat noise is large here, and a cool retest is pending.

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

### Read aloud (2026-11-02)

Pixel 10, GrapheneOS. The system voice (GrapheneOS Speech Services) gave the first audio 3.1 s after the tap, cold.
The bundled voice (developer builds only; Pocket TTS int8 through sherpa-onnx, 2 threads) ran at a real-time factor of about 1.0, against 0.2 on the desktop. It had 2 audio underruns per 25-word answer, first audio after 4.3–4.8 s, and +523–562 MB PSS. 4 threads were no faster than 2.
Phonon 31m English (`gradium/phonon-31m-en` @ `92741c5`, `model.q8.gguf`, default voice) ran as the phone's system TTS engine: Gradium's `tts-engine` app from `xn-ptts` @ `31e4e9f`, with `libptts_ffi.so` built for arm64 with dotprod and fp16, and 2 threads. The app read the same Eiffel Tower answer (12.0 s of audio) with the System voice setting. Cold, the first audio came after 2.8 s; warm, after 2.5 s (two runs). There were 0 underruns, and playback took 11.9–12.3 s. The engine process held 210–218 MB PSS idle with the model loaded and 268–270 MB at most while it spoke. The app's own PSS did not change. The first-audio times include about 0.5–1 s of polling granularity (one `dumpsys` per sample). So Phonon replaced Pocket TTS as the bundled voice.
On the desktop (2 threads, Moonshine round trip, 3 runs each), spelling numbers out before Pocket TTS cut the WER of an answer full of numbers from 13.0% to 6.1%, and of the Eiffel Tower answer from 12.5% to 8.8%.

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
| 10-23 | Gemma 4 E2B on the NPU with our dispatch library build | Pixel, 5 questions | First word 1.4–3.1 s, full answer 5.2–8.9 s | Answer model with the Gemma pack (0.3.0) |
| 10-23 | Speculative decoding on the NPU | Pixel, 3 questions each | Decode 18.6–19.0 → 27.0–30.8 tok/s, same token counts | On by default |
| 10-23 | Gemma 4 E2B against Ling | 63 seeds and 30 threads, desktop | Seeds 0.523 against 0.533, Ling 37 / 7 / 19 (p = 0.022); threads 0.640 against 0.646 | Gemma for speed, Ling stays in Settings |
| 10-23 | Gemma 4 E4B on the GPU | Pixel, 2 questions | First word 6.6–8.5 s, decode 6.5 tok/s | Not used |
| 10-23 | No warm-phone evidence cut for Gemma on the NPU | 63 seeds on the Pixel, pairwise | 23 / 17 / 17 for the full evidence; first word +0.2 s | Kept (profile `gemma4-npu`) |
| 10-23 | QAT GGUF, a COMPUTED rule, 1,000 evidence tokens for Gemma | 63 seeds, desktop, pairwise | No gain; see LiteRT-LM above | Not used |
| 10-23 | Gemma answer prompt after Google's E2B RAG sample | 63 seeds, desktop, pairwise | 0.516 against 0.526; 27 / 14 / 22 (p = 0.57) | Dropped |
| 10-23 | Reference shelf with Gemma on the NPU | 63 seeds on the Pixel, pairwise | 0.511 against 0.499; 28 / 14 / 19 (p = 0.24); travel 0.41 → 0.53 | Recommended in the README |
| 10-24 | Answer target of 150 to 250 words for Gemma | 63 seeds, desktop, pairwise | Median 86 → 96 words; 0.527 against 0.526; 19 / 28 / 16 (p = 0.74) | Dropped |
| 10-23 | Rerank pool of 48 or 96 instead of 24 (Ling) | 63 seeds, recall | R@ev 0.271 → 0.267 → 0.258; R@10 0.351 → 0.383 → 0.372; rerank 180 → 288 → 580 ms on the desktop | 24 |
| 11-02 | Gemma: prefill the system prompt and the question on the NPU during the search (`Session` API) | Pixel, 17 seeds × 2 per build, interleaved | First word 1,865 → 1,277 ms; decode 27.6 → 27.8 tok/s | Candidate; needs the 63-seed eval |
| 11-02 | 6 instead of 4 ONNX Runtime threads | Same runs | Rerank 391 → 485 ms, card 534 → 629 ms | 4 |
| 11-03 | `+fp16` in the arm64 ggml build | Pixel `llama-bench`, ABBA | Decode at depth 1,100 +34% (MiniCPM); Ling's +32% is heat noise; prefill unchanged | Candidate; needs the eval |
| 11-03 | MoE expert matmuls as GEMM (llama.cpp patch 0001) | Pixel `llama-bench`, perplexity | Ling prefill +20-31%; perplexity identical | Candidate; needs the eval |
| 11-03 | Thread mask, poll and count for decode | Pixel, battery power trace | 0.32-0.35 J per token in every setting | Keep today's setting |
| 11-03 | DSpark speculative decoding for MiniCPM | Pixel, 120 tokens | Break-even at best (19 against 17.5-20 tok/s), but with pool contention: invalid | Retest (morning_spec.sh) |

Known weak spots that these experiments did not fix:

- Questions about who held an office at a time: Ling answers "Donald Trump" for 2016 and "Vladimir Putin" for 2010 next to the correct term dates. Thinking mode fixed 1 of 4 such answers.
- Title traps: the reranker ranks the colour article "Sky blue" first for "Why is the sky blue?" (fp32 and int8 alike).

## Not measured yet

- The time of the underline check (rarity lookups) on the Pixel.
- The time of the Wikidata second hop on its own.
- Prefill with pinned threads and with `n_ubatch` 1024 against 512 in the app.
- Voice input with a real voice on the phone's microphone: transcript quality, stop-to-final latency, heat.
- Gemma on the NPU on a hot phone (thermal headroom above 0.85). A long session is measured in [Long sessions and heat](#long-sessions-and-heat).
