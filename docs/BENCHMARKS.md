# Benchmarks

This file holds the measurements that describe the current app: speed and memory on the phone, answer quality, and the experiments behind the design. Each measurement names its device, build and date. The acceptance device is a Pixel 10 on GrapheneOS. Desktop runs measure quality and check code paths; they say nothing about phone speed. Measurements of models and builds that the app no longer uses are in the git history of this file (`git log -p docs/BENCHMARKS.md`).

## Summary

Pixel 10 unless a row says otherwise, Ling-3.0-tiny Q4_0, the distilled Ettin-17m reranker (`model_sel8.onnx`) on 24 passages, the question rewrite off. The rows of 2026-11-02 to 2026-11-05 measure debug or scratch builds of the candidate for release 0.4.0, not the signed APK. The other rows are older, and nothing newer replaces them.

| Measure | Value | Build, date |
|---|---|---|
| Ling, final candidate against the 11-04 ship set: 9 prompts, interleaved, from skin 34 °C or less | Prefill 80.8, 82.9 → 101.4, 96.9 tok/s (+21.1%); decode 12.7, 13.0 → 14.0, 15.4 tok/s (+14.4%); first word 6.88, 6.78 → 5.14, 5.29 s (−23.6%); total 175.0, 171.5 → 135.3, 129.9 s (−23.5%); RSS −90 MB. These prompts write 18.4% fewer tokens and the 142 questions about 8% fewer, so expect about −17% in total | Debug APK `0884e702c25f`, 2026-11-05 |
| Ling, final build against the 11-04 ship set: 9 prompts, from a cool start, not interleaved with the ship set | Prefill 90.3, 92.8 tok/s (+11.9%); decode 14.9, 14.9 tok/s (+16%); first word 6.48, 5.43 s (−12.7%); total 142.6, 140.6 s against 173 s (−18.3%); answers 12% shorter | Debug APK `c23f5fcef225`, 2026-11-06 |
| Ling, final candidate in the app: 62 questions, warm phone | First word median 6.02 s, total median 10.0 s, decode 13.1 tok/s, prefill 79.4 tok/s; canary 12/12, recent 18/20, abstention 26/30, invented 1 | Debug APK `b12647aa6ec2`, 2026-11-05 |
| Gemma 4 E2B on the Tensor NPU, the same 62 questions, search changes off | First word median 1.34 s, total median 4.23 s, decode 25.8 tok/s; canary 12/12, recent 18/20, abstention 26/30, invented 1 | `x-inapp-rel-gemma`, debug APK `c23f5fcef225`, 2026-11-06 |
| MiniCPM5-2B, the same 62 questions, search changes off | First word median 7.78 s, total median 15.78 s, decode 7.3 tok/s, prefill 54.1 tok/s; canary 12/12, recent 17/20, abstention 23/30, invented 2 | `x-inapp-rel-minicpm`, debug APK `c23f5fcef225`, 2026-11-06 |
| Gemma prefill during the search: 17 seeds, interleaved | First word 1,865 → 1,277 ms, sooner on all 17 questions; decode 27.6 → 27.8 tok/s | Debug builds on `ac3829e` (uncommitted), 2026-11-02 |
| `llama-bench`, stock against the candidate's llama.cpp | Ling prefill +50% (depth 0) to about 2× (depth 1,100), Ling decode at depth about +60%, MiniCPM decode at depth about +30% | `spike/040-candidate` on `ac3829e`, 2026-11-03 |
| Frozen candidate in the app: five questions, not interleaved | First word 3.48 to 3.85 s on normal questions; 4.86 s and 3.14 s on how-to questions; no errors | Debug APK `171c7328b7bd`, 2026-11-05 |
| Phonon read aloud, freed after each answer | Load 175 and 123 ms, first audio 201 and 206 ms; RSS 5,311 MB before, peak 5,462 to 5,533 MB while reading, 5,333 to 5,393 MB after each reading (limit: 6 GiB with the app on screen) | Debug APK `171c7328b7bd`, 2026-11-05 |
| Release gate, frozen candidate, Ling (desktop, 142 questions, two rolls, reference: the ship prompt at 400 tokens) | PASS, but not a clean pre-registered result: canary 24/24, unverified 10.2% against 10.7%, expected 95/106 against 95/106, abstention 52/60 against 45/60, pairwise 20/18/22, p 0.88, +0.03 [−0.27, +0.40] | `v4-rc-ling`, `v4-rc-ling2`, `d57c4513e73b`, 2026-11-05 |
| Card (search, rerank, best passage) | Median 0.54 s with the Starter set, 0.60 s with the Reference shelf | 0.2.3 release, 2026-10-21 |
| Model start after the app was closed, Ling | 16.3–16.6 s with the saved warm-up state, 23.9–26.0 s without it | 0.2.0, 2026-10-13 |
| Model start after a force-stop, Gemma on the NPU | 9.2 s and 6.1 s | 0.3.2 release, 2026-10-24 |
| Peak RSS with Gemma on the NPU | 2.2–2.3 GB | 0.3.0, 2026-10-23 |
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
- With the Starter set, the squeaky hinge and the tap water in Mexico City got "the sources do not contain…". With the Reference shelf, travel questions got cited answers from Wikivoyage and CDC travel: tipping in Japan, tap water in Mexico City, vaccinations for Kenya, a jellyfish sting.

### In-app benchmark and model start (0.2.0, 2026-10-13)

The 0.2.0 debug build (`c552cf4`), the Starter set and the breadth packs, on USB power, 10 questions.

- First word: median 7.2 s (5.9–13.4 s). Card p50: 407 and 533 ms. Rerank of 24 passages: 270–362 and 263–605 ms.
- Decode: 16–17 tok/s on a cool phone, and 8–9 tok/s after the phone passes about 37 °C.
- Peak RSS (VmHWM): 5,315 and 5,294 MB, so 5.3 GB.
- Time from launch to "Ready": 26.0, 23.9 and 23.9 s without the saved warm-up state, and 16.6, 16.6 and 16.3 s with it. The state file is 27.8 MB. The rest of the start is the load of the 4.9 GB model into RAM.

### Long sessions and heat

Three conversations of 25 questions each, typed through the app's UI with no pause, on USB power. They hold follow-ups, topic switches, typos, false premises, units and recent facts. The 0.2.0 debug build (`be6d684`), 2026-10-15:

| Questions | First word, median | Decode | Tap to full answer, median |
|---|---|---|---|
| 1–10 | 13 s | 8.7 tok/s | 34 s |
| 31–40 | 19 s | 5.2 tok/s | 57 s |
| 61–75 | 28–30 s | 4.8 tok/s | 63–65 s |

75 of 75 answered, with no crash. Later builds read 400 evidence tokens on a hot phone and run no planner. Session s2 of the same test, typed the same way (25 questions; 29 on 2026-10-19):

| | 0.2.1 | `2bba986` | `c2717c0`, Ling | `c2717c0`, MiniCPM5-2B |
|---|---|---|---|---|
| Crashes | 0 | 0 | 0 | 0 |
| First word, median | 29 s | 18 s | 14.7 s | 11.7 s |
| Tap to full answer, median | 67 s | 41 s | 40.3 s | 42.6 s |
| PSS at the end | 5.50 GB | 5.45 GB | 5.40 GB | 2.55 GB |

Session s2 was the second session in the first runs, so those runs started on a hotter phone. RSS grows by about 9 MB per question: RssAnon stays flat, and RssFile grows with pages of the memory-mapped indexes, which the kernel can reclaim.

Gemma 4 E2B on the NPU (0.3.0 debug build, 2026-10-23): the phone eval runs `x-gemma4-phone` and `x-gemma4-phone2` asked the 63 seeds two times. That is 126 questions in 76 minutes, each in a new conversation, on USB power. Thermal headroom rose from 0.57 to 0.83.

- On 11 paired seeds, the first word stayed at 1.86 and 1.83 s from headroom 0.63 to 0.78. Decode stayed at 27.5 and 27.0 tok/s. The slowest first word of the 126 answers was 2.76 s.
- The headroom never reached 0.85, where Ling's session stayed from question 10 on. The NPU worked about 7 s of every 36 s, and Ling's session answered with almost no pause.

### `llama-bench`

Pinned to the cores of the mask, repacked, in RAM, 3 repetitions, on USB power.

| Model | Mask | Threads | Prefill pp512 | Decode tg64 | Date |
|---|---|---|---|---|---|
| Ling-3.0-tiny Q4_0 | 0xFC | 4 | 95.0 ± 21.6 tok/s | 23.1 tok/s | 2026-09-29 |
| Ling-3.0-tiny Q4_0 | 0xFC | 6 | 122.7 ± 16.6 tok/s | 26.7 tok/s | 2026-10-19 |
| MiniCPM5-2B, pure Q4_0 | 0xFC | 4 | 137.4 tok/s | 19.9 tok/s | 2026-10-19 |
| MiniCPM5-2B, pure Q4_0 | 0xFC | 6 | 190.6 tok/s | 21.0 tok/s | 2026-10-19 |
| MiniCPM5-2B, mixed Q4_0 (bartowski) | 0xFC | 6 | 166.6 tok/s | 19.5 tok/s | 2026-10-19 |

- llama.cpp repacks only Q4_0 tensors for ARM. So the pure MiniCPM file (all 296 matrices Q4_0) prefills 12–15% faster and decodes 8–9% faster than the mixed file.
- Weights must be repacked: LFM2.5-8B-A1B ran at 51 tok/s prefill with plain weights against 120 tok/s repacked (2026-09-29). mmap plus repacking peaked at 7.0 GB of RAM, so the app reads the model into RAM (`LLAMA_LOAD_MODE_NONE`).

### Reranker on the phone

| File | Rerank on the Pixel | Date |
|---|---|---|
| `model_qint8_arm64.onnx` (shipped until 2026-10-02), 40 passages | 509 ms (cool) to 787 ms (hot) | 2026-10-02 |
| `model_nb8.onnx`, 40 passages | 1,185 ms | 2026-10-02 |
| `model.onnx` (fp32), 40 passages | 1,410 ms | 2026-10-02 |
| `model_nb8.onnx`, 24 passages, in-app benchmark | median 424 ms (392–851) | 2026-10-02 |
| `model_sel8.onnx` (distilled, the current file), 24 passages, in-app benchmark | median 297 ms (263–605) | 0.2.0, 2026-10-13 |

### Small parts

- Featured snippet: the int8 DeBERTa-v3-xsmall reader on the top 3 passages takes 234 ms on the Pixel (2026-09-29).
- Wikidata second hop: the multi-hop seeds gave cards of 663 ms and 475 ms and first words at 11.2 s and 10.5 s (`be6d684`, 2026-10-14).
- Voice input: app PSS 5.12 GB before the mic tap, 5.70 GB with the mic on, 5.16 GB after the stop tap (2026-09-29).
- Thinking mode: 384 reasoning tokens took 34 s at 14 tok/s (2026-09-29). The default cap is now 256 tokens, about 18 s.

### LiteRT-LM: Gemma 4 on the GPU and the Tensor NPU (2026-10-22 to 2026-10-23)

LiteRT-LM 0.18.1 on the Pixel 10 with the Starter Wikipedia, in a debug build with its own package name. Thermal headroom was 0.60–0.75.

| Model, file, accelerator | First word | Prefill | Decode | Full answer | Peak RSS | Result |
|---|---|---|---|---|---|---|
| Gemma 4 E2B, G5 file, NPU, speculative decoding | 1.4–3.1 s | 1,080–2,030 tok/s | 27.0–30.8 tok/s | 5.2–7.6 s | 2.2–2.3 GB | Correct, cited answers |
| Gemma 4 E2B, G5 file, NPU | 1.5–2.3 s | 795–2,080 tok/s | 18.6–19.0 tok/s | 7.9–8.9 s | 2.2–2.3 GB | Correct, cited answers |
| Gemma 4 E2B, generic file, GPU (OpenCL) | 3.1–4.8 s | about 420–610 tok/s | 9.5–9.6 tok/s | 16.6–20.3 s | 2.1 GB | Correct, cited answers |
| Gemma 4 E4B, generic file, GPU | 6.6–8.5 s | about 230–260 tok/s | 6.5–6.6 tok/s | 21.1–25.7 s | 3.1 GB | Correct, cited |
| Gemma 4 E2B, generic file, CPU (0.17.1, 2026-09-29) | 4.5–5.6 s | about 170–200 tok/s | 9.5–10.5 tok/s | — | 2.8–2.9 GB | Works |

- The NPU needs `libLiteRtDispatch_GoogleTensor.so` from the LiteRT commit that LiteRT-LM pins (`ca21dc99`). LiteRT-LM 0.17.1 and 0.18.1 crash in Google's v2.2.0 library. Our build from `ca21dc99` works ([MODELS](MODELS.md#runtimes)).
- Speculative decoding on the G5 file gave the same token counts and 1.5 times the decode speed. Its effect on the GPU and CPU is not measured.
- Answer quality on the Pixel, 63 seeds asked in the app (0.3.0 debug build, the Starter set, 2026-10-23): a score ratio of 0.516 with the warm-phone cut to 400 evidence tokens (`x-gemma4-phone`), and 0.499 with the full evidence (`x-gemma4-phone2`). Pairwise against Ling's desktop run `v22-answer-rules`, Ling won 35 / 7 / 15 (p = 0.007) with the cut. With the full evidence, Ling won 33 / 7 / 21 (p = 0.134, mean +0.53 [−0.17, +1.25]). Ling won all 5 numeric seeds, because Gemma often leaves out the COMPUTED value.
- The phone answers match the desktop stand-in: pairwise 25 / 13 / 19 against the Q4_0 GGUF run (`x-gemma4-e2b`, 0.523).
- Numbers on the Pixel (debug build, 2026-10-23): a speed in the question and an entity's length now give a COMPUTED time. On the 13 numeric and comparison seeds, pairwise against `x-gemma4-phone2`: 2 / 9 / 1 for the change, mean 8.6 against 7.6, p = 1.0 (`x-gemma4-phone-num`). The Great Wall seed rose from 2.0 to 9.5.
- The Reference shelf on the Pixel (debug build with the number changes, 2026-10-23): 0.511 against 0.499 with the Starter set alone (`x-gemma4-phone-shelf` against `x-gemma4-phone2`). Pairwise 28 / 14 / 19 for the shelf, mean +0.57 [−0.17, +1.32], p = 0.24. Travel rose from 0.41 to 0.53 (7 / 0 / 1), the same gain as Ling's. How-to won 5 / 0 / 2, but its ratio stayed at 0.41. Factual fell from 0.47 to 0.41.
- Conversations, on the desktop with the same GGUF (`x-gemma4-cv-test`, 2026-10-23): 0.640 on the 30 locked test threads against Ling's 0.646, and 0.597 against 0.589 without the small-talk threads.
- The 0.3.2 test app on the Pixel with the Starter set, the Reference shelf and Gemma (2026-10-24): the 63 seeds gave the same answers, word for word, as `x-gemma4-phone-shelf` on 0.3.0 (`x-032-phone-full`). With the same evidence, Gemma on the NPU writes the same words.
- Conversations on the Pixel (0.3.2 test app, the same packs, 2026-10-24): 0.605 on the 30 locked test threads and 0.555 without the small-talk threads (`x-032-phone-cv`). First word 1.96 s and full answer 6.53 s (medians).
- The import of `gemma4-e2b-npu.tar` (3.1 GB) from Downloads took 28 s.
- llama.cpp on the PowerVR GPU was never run here. Planning-stage numbers (about 24 tok/s prefill, 2.5 tok/s decode, no integer dot product in the driver) ruled it out.

### Gemma prefill during the search (2026-11-02)

Pixel 10, `app.commonplace.spike` debug builds of the branch `spike/npu-overlap` on `ac3829e` (uncommitted changes), enwiki-core, wikidata-facts and `gemma4-e2b-npu`, speculative decoding on. 17 seed questions, each as a new topic. The builds ran in the order A, B, C, A, B, C. Thermal headroom was 0.5–0.8. The table gives the medians of 34 questions per build.

| Build | Card | Rerank | Reader | Prefill after the card | First word | Decode |
|---|---|---|---|---|---|---|
| C: `Conversation`, no prefill during the search | 501 ms | 384 ms | 188 ms | 1,181 ms | 1,865 ms | 27.6 tok/s |
| A: `Session`, system prompt and question prefilled during the search | 534 ms | 391 ms | 209 ms | 539 ms | 1,277 ms | 27.8 tok/s |
| B: as A, 6 encoder threads instead of 4 | 629 ms | 485 ms | 312 ms | 549 ms | 1,459 ms | 27.8 tok/s |

- A gave the first word sooner on all 17 questions (paired medians): by 566 ms at the median, 54 to 793 ms.
- Answer length per question did not change (paired median −1 token). Answer quality is not judged.
- Six encoder threads made the rerank 95 ms and the reader 120 ms slower, so the app keeps 4.

### CPU path: build flag, MoE prefill and energy (2026-11-02 to 2026-11-03)

Pixel 10 unless a row says otherwise. The builds are llama.cpp b12032 built with the app's flags, branch `spike/cpu-speed` on `ac3829e` (uncommitted), `-C 0xFC --cpu-strict 1`, repacked weights in RAM. Depth means the tokens already in the context. The app runs at about depth 1,100. [Release 0.4.0 candidate](#release-040-candidate-2026-11-03) gives the speed of the whole patch series on the Pixel.

| Patch or flag | Result | Ships in 0.4.0 |
|---|---|---|
| `+fp16` in `GGML_CPU_ARM_ARCH` | Decode at depth 1,100: MiniCPM +34%. Ling's +32% is unreliable, because its row shows depth 1,100 faster than depth 0. Prefill does not change. The table below has the numbers | Yes |
| 0001, MoE expert matmuls as GEMM | Ling, 6 threads: prefill 167 → 201 tok/s at depth 0, 88 → 115 tok/s at depth 1,100. Decode and perplexity do not change (9.2871 on 8 chunks, desktop) | Yes |
| The rest of 0001-0008 | QEMU arm64 with 0001-0008: max KLD 2.9e-5 (MiniCPM5) and 3.7e-5 (Ling), same top token 100%. Phone speed of 0005 to 0008 alone is not measured | Yes |
| 0009, Q5_0 through ARM tinyBLAS | QEMU: Ling's logits move (KL mean 0.002, max 0.011, same top token 98.4%). A direct matmul check gives max relative error 2.6e-8 with 0009 and 2.0e-8 without. Against x86 logits it is as close as stock, so the KL is sensitivity to rounding. Not measured alone on the phone | Yes |
| 0010, MoE expert sum as one fused pass | Desktop: KL at the floor (max 4.8e-5), decode unchanged within noise. Not measured alone on the phone | Yes |
| 0012, batches of 2-63 queries through tiled flash attention | Desktop, 4 threads, depth 4000, 8 tokens: MiniCPM5 167.3 → 103.8 ms, Ling 83.4 → 67.9 ms. Decode and 512-token prefill do not change. Pixel (`p1to12`, 11-05): no gain at 2 tokens, and its other rows hold stalls | Yes |
| 0013, fewer barriers | Barriers per token: Ling 1,577 → 903, MiniCPM5 718 → 716. KL at the floor. x86 decode: Ling −1.8% at depth 0, −0.9% at 1100. Not measured alone on the phone | Yes |
| 0002, the draft context shares the target's pools | Desktop, 4 cores: plain decode 46.3 tok/s, stock 7.4, own pools 5.2, shared pools 47.2 | In the series; no CPU model uses a draft model |
| 0011, KleidiAI leaves Q6_K to the repack buffer | QEMU, Ling, against x86 logits: perplexity 11.122 stock, 11.085 with 0001-0010, 10.936 with 0011 (x86 11.042). Load 33.5 s → 18.6 s | No |
| A thread pauses its previous pool | Desktop: draft threads on their own pools 16.7 → 37.4 tok/s | No. The number 0014 now holds the last-layer patch |
| DSpark drafter for MiniCPM5 (Q4_0) | Pixel: 2.2-2.6 tokens per step, best runs 19 against 17.5-20 tok/s. These runs had pool contention, so they do not count. Desktop at the app's sampling: about 2.5 tokens per step at length 3, about 2.8 at length 5 | No |
| Prompt lookup (`decode_ngram`) | Desktop, MiniCPM5: 1.56 tokens per step, CLI 33.9 tok/s plain, 29.3 and 29.7 with lookup. Ling: 1.33 tokens per step with 8 state snapshots (about +170 MB) | No. The 11-05 verify row closes it for Ling |
| Thread mask, poll and count | Ling decode at depth 1,100, from a battery current trace: 0.32-0.35 J per token in every setting. Fewer cores lower the power and the speed together. The existing setting fell from 18.1 to 13.1 tok/s in 7 minutes of nonstop decode | The existing setting stays |

`+fp16`, ABBA order, two runs per build, skin 40-42 °C, 4 threads decode, 6 threads prefill, tok/s:

| Model | Build | Decode d0 | Decode d1100 | Prefill pp512 d0 | Prefill d1100 |
|---|---|---|---|---|---|
| Ling | app flags | 17.6 | 12.2 | 113.4 | 64.9 |
| Ling | +fp16 | 15.6 | 16.1 | 106.2 | 61.9 |
| MiniCPM | app flags | 11.7 | 8.0 | 99.7 | 59.8 |
| MiniCPM | +fp16 | 11.6 | 10.7 | 98.8 | 58.4 |

- Runs of one Ling build varied by up to 30% in this set, so the MiniCPM gain is the firm one. The desktop loses only 7% without `+fp16` (Ling 61.3 against 57.2 tok/s), and the phone ran 24-25% slower.
- The medium cores are two clock domains (cpus 2-4 and 5-6). A pool pinned across them waited for cpus 5-6 at 921 MHz while cpus 2-4 ran at 2.9 GHz.
- A static NDK-clang build under `qemu-aarch64 -cpu max,sme=off,sve-default-vector-length=16` reproduced the Pixel's greedy output byte for byte on two prompts (94 and 136 words). A clang 21 aarch64-linux build did too. A GCC 15 build diverged at word 90 of 94 and at word 4.
- Desktop profile of Ling decode (x86, 4 threads): routed experts 37%, output head 21%, Q8_0 matmuls 14%, `ggml_barrier` 7.3%. Every Q8_0 and Q6_K tensor as Q4_0 cost perplexity +1.6%, KL 0.029, same top token 91.6%, so we rejected it.

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
- The Reference shelf (`v23` against `v22`) raises travel from 0.41 to 0.53 and long tail from 0.40 to 0.56. Pairwise on all 63 seeds it is 27 / 9 / 27, with a mean gain of 0.52 points [−0.08, +1.13]. So the overall gain is not significant (2026-10-23).
- With the older engine on 27 seeds (2026-10-10), the full pack set beat the Starter set 17 / 2 / 6 pairwise (p = 0.035).
- Model ceiling: Claude Sonnet, given exactly Ling's evidence on the 25 seeds with full evidence, scored 0.777 against Ling's 0.639 (2026-10-10). Across all 63 seeds that gap is worth about +0.05.

### Small models and their chat format (desktop, 2026-11-03)

`dev.jsonl` (60 questions), `--phone`, the full library, Opus judge. Build `ac3829e` (old format) against `ac3829e-dirty` on branch `spike/lfm3-prompt` (new format). The new format adds the BOS token when the chat template writes `bos_token` but the GGUF has `add_bos_token = false`. Ling and Gemma prompts do not change. Times are desktop medians.

| Run | Model and format | Ratio | Median answer | First word | Full answer |
|---|---|---|---|---|---|
| `v2-dev-ling` | Ling | 0.646 | 408 chars | 2.26 s | 4.71 s |
| `x-minicpm-dev-bos` | MiniCPM5-2B pure Q4_0, with BOS | 0.621 | 310 chars | 2.50 s | 4.42 s |
| `x-minicpm-dev-old` | MiniCPM5-2B pure Q4_0, as shipped | 0.602 | 336 chars | 2.51 s | 4.59 s |
| `x-lfm3-dev-think` | LFM3-1.7B QAD Q4_0, new format, thinking on (budget 256) | 0.576 | 460 chars | 6.35 s | 8.68 s |
| `x-gemma-dev` | Gemma 4 E2B Q4_0 | 0.567 | 508 chars | 2.40 s | 5.71 s |
| `x-lfm3-dev-off` | LFM3, new format, thinking off | 0.524 | 716 chars | 1.67 s | 5.12 s |
| `x-spark-dev` | Spark-X2.6-1.7B Q4_0, its own format, thinking off, our sampling | 0.478 | 1,311 chars | 2.03 s | 8.60 s |

- MiniCPM with BOS against as shipped: `pairwise.py` gives 23 / 16 / 21 (p = 0.88, mean diff −0.13), so no gain. The fix stays because it matches the model's own chat template.
- LFM3 with thinking is behind MiniCPM with BOS (18 / 11 / 31), and its first words come 3.8 s later. Spark-X2.6 lost to MiniCPM with BOS 11 / 7 / 42 and to Gemma 11 / 9 / 40.

### Release 0.4.0 candidate (2026-11-03)

Branch `spike/040-candidate` on `ac3829e`: llama.cpp patches 0001-0010, 0012 and 0013 (`patches/llama.cpp/`), `+fp16` in the arm64 build, Gemma's prefill during the search (`LlmBackend::prepare`) and the MiniCPM BOS fix. Desktop runs use x86, so they do not exercise `+fp16` or the arm64 kernels.

- Desktop, Ling, dev split (`x-040-dev-ling` against `v2-dev-ling`): 43 of 60 answers identical, pairwise 6 / 44 / 10 (p = 0.45).
- Desktop, MiniCPM, dev split (`x-040-dev-minicpm` against `x-minicpm-dev-old`): pairwise 23 / 11 / 26 (p = 0.78, mean diff −0.21).
- Desktop, Ling, reference set (142 questions, `x-040-ref-ling` against `x-master-ref-ling`): 122 identical, pairwise 8 / 124 / 10 (p = 0.82, mean diff +0.01).
- Pixel 10 `llama-bench` (`scripts/bench/ship_bench.sh`), stock against the candidate's llama.cpp. The order is A B B A per model, skin 36-38 °C for all but the first run, in tok/s:

| Model | Build | pp512 d0 | pp512 d1100 | tg64 d0 | tg64 d1100 |
|---|---|---|---|---|---|
| Ling | stock | 139.8, 155.2 | 70.3, 88.0 | 18.7, 24.5 | 12.1, 11.6 |
| Ling | 0.4.0 | 224.6, 225.3 | 163.3, 167.4 | 12.8, 24.8 | 17.8, 20.3 |
| MiniCPM | stock | 167.5, 123.3 | 110.7, 68.5 | 20.9, 12.9 | 13.8, 8.4 |
| MiniCPM | 0.4.0 | 173.5, 167.1 | 128.8, 111.8 | 11.2, 10.2 | 18.0, 11.1 |

Ling prefill +50% (depth 0) to about 2× (depth 1,100), Ling decode at depth about +60%, MiniCPM decode at depth about +30%. MiniCPM decode at depth 0 was lower with 0.4.0 in both pairs. Heat noise is large here.

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

Moonshine Medium Streaming EN on a 44 s LibriVox clip, streamed in 100 ms chunks at real time. WER was 2.5–4.2% over four runs. The first live text came 1.8 s after the audio starts, and the final text 0.33–0.46 s after a stop in the middle of a sentence. 8 s of silence gave no text. Memory: 750 MB after load, 830–925 MB peak.

### Read aloud (2026-11-02)

Pixel 10, GrapheneOS. The system voice (GrapheneOS Speech Services) gave the first audio 3.1 s after the tap, cold.

The bundled voice of that time (developer builds only; Pocket TTS int8 through sherpa-onnx, 2 threads) ran at a real-time factor of about 1.0. It had 2 audio underruns per 25-word answer, first audio after 4.3–4.8 s, and +523–562 MB PSS. 4 threads were no faster than 2.

Phonon 31m English (`gradium/phonon-31m-en` @ `92741c5`, `model.q8.gguf`, default voice) ran as the phone's system TTS engine: Gradium's `tts-engine` app from `xn-ptts` @ `31e4e9f`, `libptts_ffi.so` for arm64 with dotprod and fp16, 2 threads. The app read the same Eiffel Tower answer (12.0 s of audio). Cold, the first audio came after 2.8 s; warm, after 2.5 s (two runs). There were 0 underruns, and playback took 11.9–12.3 s. The engine process held 210–218 MB PSS idle and 268–270 MB at most while it spoke. The first-audio times include about 0.5–1 s of polling granularity. So Phonon replaced Pocket TTS as the bundled voice.

On the desktop (2 threads, Moonshine round trip, 3 runs each), spelling numbers out before Pocket TTS cut the WER. An answer full of numbers went from 13.0% to 6.1%, and the Eiffel Tower answer from 12.5% to 8.8%.

## Experiments and decisions

Each row is a change that we tested. "Kept" means the app has it now. Dates are in 2026. [CPU path](#cpu-path-build-flag-moe-prefill-and-energy-2026-11-02-to-2026-11-03) holds the decisions on patches and build flags, and [LiteRT-LM](#litert-lm-gemma-4-on-the-gpu-and-the-tensor-npu-2026-10-22-to-2026-10-23) holds the details of the Gemma rows.

Rows from 11-03 on share this setup unless a row says otherwise: Ling, the desktop, `--phone`, Opus judge, and a clean or scratch build of `09bb52e` with the working tree (`v0.3.2-29-g09bb52e-dirty`). The `meta.json` file of each run in `eval/runs/` holds the binary's sha256. Short names:

- Gated set: 142 questions of test, canary, abstention, robust and recent.
- Outside set: 142 questions of dev, canary, abstention, recent and robust, outside the locked split.
- Set of 102: dev, canary and abstention. `x-pr-base` is the ship prompt on this set: cached 1,300, new 503, generated 10,218, abstention 24/30, invented 3.
- Evidence set: 100 questions of dev, practical and recent, with Sonnet as the evidence judge and `--cli=--evidence-only`.
- `perf`: the 9 prompts of `scripts/bench/perf.jsonl`.
- Pixel interleaved: Pixel 10 (GrapheneOS), Ling on the CPU, one debug APK with the arm chosen by a file, `perf`. The order is A, B, A, B, each from skin 34 °C or less. The quiet driver polls the telemetry file, not `uiautomator dump`.
- Ship: the 11-04 ship set (`x-win-gate`, `x-win-conv`). `x-T-base` is ship on the outside set: new 505, generated 13,397, abstention 25/30, expected 46/52.
- Pairwise a/b/c: better, tied and worse for the change. Brackets give the 95% interval of the mean difference on a scale of 10.

| Date | Change | Test | Result | Decision |
|---|---|---|---|---|
| 09-29 | Ling-3.0-tiny against LFM2.5-8B-A1B | `llama-bench`, app on screen | Ling faster on prefill and decode, 5.1 vs 5.5 GB | Ling |
| 09-29 | Maple-Preview TQ1_0 (20B, ~1B active) | 63 seeds, `llama-bench` | 0.40 against Ling's 0.44; decodes 1.65× faster, prefills 21% slower | Not used |
| 09-29 | No LLM planner (routing in code) | 10 questions, desktop | First word 8.9 → 3.0 s; on the Pixel one planner call took 19–69 s | Kept; the planner is removed (10-15) |
| 10-02 | Question rewrite by the LLM | Pixel card, 63 seeds | Card 2.0 → 1.0 s without it; seeds 0.508 with, 0.521 without | Off by default |
| 10-02, 10-23 | Rerank pool of 24 instead of 40, 48 or 96 | 400 NQ questions; 63 seeds, recall | 24 against 40: hit@5 0.710 against 0.713. 24, 48, 96: R@ev 0.271 → 0.267 → 0.258; R@10 0.351 → 0.383 → 0.372; rerank 180 → 288 → 580 ms on the desktop | 24 |
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
| 10-22 | General knowledge without citations, with the underline check; a [0] mark instead | 63 seeds, 92 threads | 0.547 / 0.533 against 0.546 (noise); threads 32 / 31 / 29. With the mark, Ling never wrote it: 0.512 | Kept without the mark |
| 10-22 | Underline threshold 0.6 of the rarity weight; a second signal (leaf-mt cosine, reranker score) | 123 sentences labeled by Claude | Underlines 49, 37 of them outside the evidence (precision 0.76). With the second signal, wrong and right underlines score the same | Threshold kept, signal not used |
| 10-22 | Check turns ("are you sure?"), which also search the checked answer's first claim | 10 and 11 threads, pairwise | 5 / 3 / 2, mean 6.45 against 4.50; with the search 7 / 2 / 2, mean 8.00 against 5.67 [+0.27, +3.82] | Kept |
| 10-22 | Rules against restating the earlier answer and to check dates against the question's year | 24 threads; 16 office-holder questions | 9 / 6 / 9; 12 of 16 right with and without the rule | Dropped |
| 10-22 | Swap the card to the first cited source | 62 seed answers | 19 do not cite source 1, but only about 3 of those cards are wrong | Not done |
| 10-23 | Gemma 4 E2B on the NPU with our dispatch library build; E4B on the GPU | Pixel, 5 and 2 questions | E2B: first word 1.4–3.1 s, full answer 5.2–8.9 s. E4B: first word 6.6–8.5 s, decode 6.5 tok/s | E2B is the answer model with the Gemma pack (0.3.0) |
| 10-23 | Speculative decoding on the NPU | Pixel, 3 questions each | Decode 18.6–19.0 → 27.0–30.8 tok/s, same token counts | On by default |
| 10-23 | Gemma 4 E2B against Ling | 63 seeds and 30 threads, desktop | Seeds 0.523 against 0.533, Ling 37 / 7 / 19 (p = 0.022); threads 0.640 against 0.646 | Gemma for speed, Ling stays in Settings |
| 10-23 | No warm-phone evidence cut for Gemma on the NPU | 63 seeds on the Pixel, pairwise | 23 / 17 / 17 for the full evidence; first word +0.2 s | Kept (profile `gemma4-npu`) |
| 10-23, 10-24 | For Gemma: QAT GGUF, a COMPUTED rule, 1,000 evidence tokens, Google's E2B RAG prompt, an answer target of 150 to 250 words | 63 seeds, desktop, pairwise | `x-gemma4-qat` 0.505 against 0.523, 21 / 24 / 18; `x-gemma4-qat-prompt` 23 / 24 / 16; `x-gemma4-qat-ev1200` 26 / 16 / 21; `x-gemma4-d-prompt` 27 / 14 / 22 (p = 0.57); `x-gemma4-d-len` 19 / 28 / 16 (p = 0.74) | None used |
| 10-23 | Reference shelf with Gemma on the NPU | 63 seeds on the Pixel, pairwise | 0.511 against 0.499; 28 / 14 / 19 (p = 0.24); travel 0.41 → 0.53 | Recommended in the README |
| 11-03 | Turn-kind head retrained: 92 more reformat examples, 66 questions that read like reformat requests (reformat bar 0.806); the entity guard lets a reformat through when the follow-up head reads that it needs the last turn | `commonplace turn`, conversation sets, int8 encoder | Tuning threads 87 → 89 of 92, locked test 26 → 27 of 30, reformat requests 13 → 16 of 22; seeds 63 of 63; 14 untrained look-alike questions 9 → 13 right | Kept |
| 11-03, 11-04 | Sentence head on the reranker's token states picks the evidence, not word overlap | Evidence set, `x-win-500`, `x-win-400`; gated set, `x-ext-sent-gate` | Full evidence of 100 (mean tokens): overlap 600 62 (579), windows 500 63 (484); overlap 400 56 (378), windows 400 59 (376). Global fill 500 66 (522), but on the gated set explanation 1/0/7, because fragments mix up the mechanism. Windows 500 against overlap 600: 5 better, 5 worse. Under 1 ms per question. Three gap-fill fixes for how-to at 400 gave 8, 8, 8 full against 10 | One window of 120 tokens per passage, 500 / 400 tokens on CPU models: same completeness as overlap 600 with 95 fewer tokens |
| 11-03, 11-04 | Learned fusion instead of RRF before the 24-passage pool | 161 labeled questions (Sonnet grades), NQ-open, dev retrieval; gated set, `x-ext-fus-gate` | nDCG@8 after the rerank 0.760 → 0.812 with Stack Exchange (95 better, 44 worse, p < 0.001), 0.756 → 0.788 with the reference library (86 / 52, p = 0.005); NQ hit@5 0.693 → 0.712 and 0.698 → 0.715 (600 questions); dev R@40 0.465 → 0.495, but R@ev 0.335 → 0.301; rerank +7 ms. Gated set 36/63/43, p 0.50 | Kept |
| 11-03, 11-04 | Short answers when the intent head reads lookup or calc | Sonnet length labels on 163 questions; gated set, `x-ext-short-gate`; Pixel, ship-set APK | Lookups and calcs: 86 of 94 labeled short. Gated set 24/81/37, p 0.12, factual 2/2/5, numeric 0/3/5. Above headroom 0.85 it starts after about 20 answers in a row. A separate length head: accuracy 0.79, bar for "short" 0.973, so it never answers short | Hot phone only, then removed on 11-06 |
| 11-03 | Answerability head on the reranker logits | 382 questions judged by Sonnet, 5-fold | Precision for "not answerable" 0.84 at recall 16 of 93 (0.87 at 34 of 93 with intent); the bar is 0.9 | Not used |
| 11-04 | Ship set: learned fusion, turn-kind retrain, sentence-head windows, against all off | Gated set, `x-win-gate` against `x-ext-off-gate`; 30 locked threads, `x-win-conv` against `x-ext-off-conv`; `perf` off-on-off-on | 56/35/51, p 0.70, mean 7.74 against 7.65, −0.32 to +0.50. Score ratio 0.68 (all off 0.68). Gate against `v2-ref-ling`: **PASS**, canary 12/12, unverified 9.3% (reference 9.6%, all off 16.2%), expected 49/53, abstention 24/30, pairwise 25/9/26, p 1.00. Threads 15/7/8, p 0.21, mean 7.54 against 7.21. Desktop speed: evidence 5,223 → 4,235 tokens (−19%), first word 1.93 → 1.71 s (−11%), tap to last word 44.5 → 43.0 s (−3%) | No significant quality change. Categories of 6 to 12 questions flip between arms |
| 11-04 | The ship set on the Pixel | Pixel 10, Ling on the CPU, `perf`, debug APKs with native libs `75e931f0d728` (old defaults) and `b034bebe705e`, off-on-off-on, skin ≤ 34 °C | From 34.0 °C: answer 1,743 → 1,460 tokens, first word 6.63 → 7.01 s, tap to last word 217 → 180 s (−17%), energy 1,104 → 989 J (−10%). Headroom passed 0.70 after the first question, so both builds cut the evidence to 400 tokens | First word does not change. The gain comes from different answer lengths |
| 11-04 | Why the app runs below `llama-bench` | Same APKs, `uiautomator dump` driver, rails from `files/energy.jsonl`; desktop x86 `llama-bench` for `n_ubatch` | From 27.9 °C, first 4 answers: decode 16.5–20.5 tok/s (bench at depth 1,100: 18–20), prefill 109–139 tok/s (bench: 165), CPU rails 6.8–7.8 W. From the fifth answer, at headroom 0.75 or more: decode 10–15 tok/s, prefill about 78 tok/s, CPU rails 2.1–2.9 W. The bench gave 12–16 and 62–65 tok/s at skin 40–42 °C. Energy fit: 0.39 against 0.10 J per token. `n_ubatch` 1,024 against 512: 281.9 against 282.6 tok/s | No app overhead on decode: the gap is the phone's thermal power limit. Cold prefill stays about 30% below the bench; cause open. With the polling driver, the base first word was 7.0 s, so about 1 s of that was the driver |
| 11-04 | Answer prompt split by exact token count | Ling tokenizer, `prompts.rs` at `09bb52e-dirty`, `x-win-gate` | Cached 1,300: rules 437, examples 852. New per question, mean 502: source bodies 350, source headers 82, question at the top 18, question at the end 15. 40% of the body tokens are in sources that the answer does not cite | Measurement only |
| 11-04, 11-05 | Output head on a list of 97,168 of its 157,184 rows (61.8%, 122 MB), with exact logits for rare tokens of the prompt (patch `0015-head-rows`, `ling_head_rows.bin`) | Set of 102 and 10 language questions: `x-head-all2b`, `x-lang-all2`, `x-head-all3`, `x-lang-all3`, no judge; `perf` interleaved on the desktop | Smaller lists: 80,000 rows 100 of 102 identical, 61,536 rows 95, 43,408 rows 62 (visible typos). The list alone: 101 of 102, a Thai answer turns wrong. With the fallback: **102 of 102 identical**; languages 9 of 10, `lang-ja` changes wording. Decode 55.4, 55.3, 54.9 → 59.4, 58.4, 59.7 tok/s (**+7.4%**), prefill no change. `head_rows.py` later gave 96,496 rows, with 96,481 shared | Kept for Ling. The app keeps the shipped file, because a new run does not rebuild it byte for byte |
| 11-04, 11-05 | Self-attention of the last layer on output rows only (patch 0014) | Set of 102 (`x-att-out`); `perf` interleaved on the desktop | 101 of 102 identical; `abs-01` differs at rounding level. Prefill 368, 376, 367 → 382, 382, 383 tok/s (**+3.6%**), decode no change. Non-interleaved decode 186.6 s against 211.5 s with the same logits, so **non-interleaved runs differ by about 12%** | Kept for Ling |
| 11-04 | Fewer active experts, KV padding, sampler | Same desktop build, 4 and 6 threads | Experts, decode and prefill: 8 of 128 56.4 tok/s, 288, PPL 10.37; 6: +6%, +9%, PPL 10.77; 4: +13%, +19%, PPL 12.62. KV cells padded to 32 or 1: decode 49.2-53.9 and 51.3-52.8 against 51.9-53.7 tok/s. Sampler: about 1.5% of a token | Not used |
| 11-05 | Row list and Q4_0 output head on the Pixel | Pixel interleaved, APK `f445499ee600`, 2 rounds per arm; `x-hq4` on the set of 102 | Decode, mean against base: list **+7.4%**, Q4_0 **+12.1%**, both **+15.6%**. Prefill differences sit inside the run spread (base 96.9 and 83.9). `x-hq4`: 18 of 102 identical, 21/45/36, p 0.06, −0.37 [−0.74, 0.00], expected 27/30 (base 27/30) | Q4_0 head not used: weak evidence of a quality loss |
| 11-05 | Last-layer patch and the row list with fallback, against ship | Pixel interleaved, APK `0884e702c25f` | Same 1,460 tokens in all runs. Means: prefill +1.7%, decode **+3.6%**, first word −0.1%, total **−2.8%**, RSS 5,514 → 5,426 MB | Kept: modest and free of answer changes. Runs differ by about ±5% |
| 11-05 | **Final candidate on the Pixel** against ship | Pixel interleaved, APK `0884e702c25f` with the final candidate's text and budgets | The summary row has the numbers. End skin 39.0–39.1 °C against 39.4–39.5 °C. Answers 1,460 → 1,191 tokens | The 9 prompts overstate the gain: expect about −17% in total |
| 11-05 | Cost of a speculative verify batch at the app's depth | Pixel 10, `llama-bench` builds `p1to10c`, `p1to12`, 4 threads on cpus 2-7, depth 1,100, first clean set only | Ling: one token 47.3 ms, 2 tokens 57.8 ms (1.22×), 4 tokens 79.6 ms (1.68×), 8 tokens 123.2 ms (2.6×). MiniCPM: 53.7, 62.8 (1.17×), 73.8 (1.37×), 124.5 ms (2.3×) | N-gram drafting at about 1.33 tokens per step cannot pay on Ling. MiniCPM with DSpark may gain |
| 11-04, 11-05 | Shorter answer prompts (ship cached 1,300): `s1` shorter wording (1,005), `s3` tighter (796); `p1` = `s1` with a false-premise rule (1,024); `p2` 80 to 150 words; `t3` no Example 3, `t4` no Example 2, `t6` sources reversed | Set of 102, against `x-pr-base` | `s1` 30/35/37, p 0.46, −0.05 [−0.50, +0.41]. `s3` 31/27/44, p 0.17, −0.45 [−0.96, +0.08], abstention 21/30, invented 8. `p1` 26/38/38, p 0.17, −0.28 [−0.68, +0.14], abstention 26/30. `p2` 26/32/44, p 0.04. `t3` invented 7. `t4` 28/26/48, p 0.03. `t6` 33/17/52, p 0.05 | `p1` leads to `w1`. We rejected the others |
| 11-05 | **How much a no-meaning prompt edit moves the judge:** one word changed (`x-n1`) or two words added (`x-n2`) | Set of 102, against `x-pr-base` | `x-n1`: 15 of 102 identical, 28/40/34, −0.17 [−0.60, +0.26], comparison 2/0/4, premise 3/2/10. `x-n2`: 26 of 102 identical, 26/47/29, +0.14 [−0.23, +0.51], comparison 1/1/4, premise 4/5/6 | **Any prompt change re-rolls about 80% of answers. A difference within about ±0.3, or a category result on 6 or 15 questions, is noise.** Use the expected-answer check, abstention and a larger set for close calls |
| 11-05 | Evidence budget on `p1` | Set of 102, against `x-pr-base`; `practical.jsonl` (20), `x-prac-r1` against `x-prac-base` | 400 (`p3`): new 409, generated 8,849, 33/33/36, p 0.81, −0.00 [−0.44, +0.45]. 300 (`p6`): new 318, 29/20/53, p 0.01, −0.56 [−1.04, −0.07], explain 5/0/24, compare 1/0/5, lookup 17/14/20. `seed-fac-07` fails at 300, because the budget code skipped the Amazon River passage at rank 2. How-to 500 → 400: citations 2.10 → 1.05, 6/3/11, p 0.33. Variants `q1`, `q2`, `q4`, `q5`, `r1` to `r3`: all within noise | 400 holds quality with 19% fewer new tokens. 300 fails on explanations and comparisons. Shrink-to-fit fixes `seed-fac-07`, but everywhere it gives how-to an extra off-topic source (0/0/6) |
| 11-05 | The question above the sources | `conversation.jsonl` (92 threads), against `x-conv-base`; `r1`, `v300` | `r1` keeps it: 28/36/28, p 1.00, +0.08 [−0.28, +0.46]. `v300` removes it: 23/31/38, p 0.07, −0.28 [−0.76, +0.18], expand 1/0/11, follow-up 6/3/10 | It stays: follow-ups lose without it |
| 11-05 | Candidates before the final one, and no-meaning edits of the ship prompt: release gate | Gated set, `report.py --gate v2-ref-ling`; `x-cand-gate`, `x-g-c2` (`p1`, budgets 300 / 400 / 500); `x-w-gate`, `x-w-gate2` (`w1`, a fuller citation rule, cached 1,031); `x-g-n1`, `x-g-n2` (edits) | All six **FAIL**. Unverified (limit 10.6%): 11.4%, 14.3%; 12.5%, 11.5%; edits 12.5%, 10.3%. Expected of 53 (reference 49): 47, 50; 47, 48; edits 48, 48. Pooled against ship: candidates 150 wins, 221 losses, mean −0.28; edits 72 wins, 78 losses, mean −0.03 | **The gate's limits on unverified sentences and expected answers sit inside re-roll noise, and `x-win-gate` passed on one roll.** The candidate costs about a quarter of a point of 10. The same questions repeat across rolls, so a p value on these counts would overstate the certainty |
| 11-05 | Flat 400 or facts at 300 with `w1` (how-to 500) | Outside set, two rolls each, against `x-T-base` | Flat 400: new 414, pooled 82/109/93, mean −0.14, abstention 25/30, 27/30, expected 48, 47 of 52. Facts 300: new 369, pooled 76/100/108, mean −0.18, abstention 23/30, 25/30, expected 46, 47 of 52 | Flat 400 is simpler and holds better |
| 11-05 | **Final candidate, release gate.** Ling only: `w1`, evidence 400 (how-to 500), last-layer patch, row list with fallback. MiniCPM and Gemma keep ship behaviour (12 of 12 canaries identical) | Gated set and 30 locked threads, `x-f-gate`, `x-f-gate2`, `report.py --gate v2-ref-ling`; wider number check `x-final-gate`, `x-z-1`, `x-z-2` | `x-f-gate`: **FAIL on unverified only**, canary 12/12, 11.7% (limit 10.6%), expected 50/53 (49), abstention 25/30 (24). `x-f-gate2`: **FAIL on unverified only**, 12/12, 11.0%, 49/53, 27/30. Pooled against ship 71/112/101, mean −0.18. New 502 → 406. Threads 6/9/15, −0.07 [−0.73, +0.80]; 7/11/12, +0.10 [−0.52, +0.92]. Wider number check: 11.7% → 11.2% **FAIL**, 11.0% → 10.6% **PASS** | The reference keeps its 9.6% under the old check, so this comparison favors the candidate. The wider check removes a few false flags and changes no answer |
| 11-05 | Final candidate against what a warm phone ran before it (ship prompt at 400 tokens) | Gated set, `x-warm1`, `x-warm2` (no-meaning edit), pairwise against `x-warm1` | Warm ship: abstention 22/30, 23/30; expected 59, 60 of 65; citations 2.87, 2.70. Final: 25/30, 27/30; 62, 61; 3.08, 2.99. Pairwise 37/53/52, +0.15 [−0.16, +0.47]; 33/58/51, +0.24 [−0.08, +0.57]; the edit 41/75/26, +0.26 [+0.01, +0.51], so `x-warm1` is a low roll | **No loss against the warm phone's earlier behaviour.** The −0.18 compares with 500 tokens, which a phone uses only for its first answer |
| 11-05 | Variants on the final candidate: `x1` number rule; no pack names or section titles in headers (`x-a1`, `x-a1d`); middle prompt `m1` (cached 1,085, `x-ma`, `x-mad`, `x-mb`, `x-mbd`) | Outside set, two rolls, against `x-T-base` | `x1`: flagged sentences 46, 45 against 47, 42. Headers: cached 993, new 386, pooled 73/91/120, mean −0.38. `m1` flat 400: 44/36/62, p 0.10, −0.14 [−0.47, +0.20]. `m1` facts 350, others 450: 40/34/68, p 0.009, −0.45 [−0.80, −0.10] | All rejected |
| 11-05 | The candidate on the other models | MiniCPM5-2B pure Q4_0 and Gemma 4 E2B Q4_0 (desktop stand-in, `--profile=gemma4-npu`), set of 102 | MiniCPM: new 460 → 339, 29/30/43, p 0.13, −0.46 [−0.98, +0.05], abstention 23/30 → 20/30, invented 4 → 6, unverified sentences 11 → 24. Gemma: generated 11,759 → 13,341, 32/37/33, p 1.00, −0.10 [−0.39, +0.19], abstention 23/30 → 22/30 | Gemma holds and MiniCPM looks worse, so the candidate is for Ling only |
| 11-05 | **Final candidate in the app, all three models** | Pixel 10, Starter packs, debug APK `b12647aa6ec2`, 62 questions, driver polling `uiautomator`, `x-inapp-final-*`; against `x-inapp-ship` (`51257d4ad89c`, not interleaved) | The summary rows have the final numbers. Ling ship: canary 12/12, recent 18/20, abstention 25/30, invented 3, 3,782 tokens, first word 6.85 s, total 12.7 s, 10.8 tok/s, 68.3 tok/s; final 3,968 tokens | Holds every check in the app, on ARM. Warm phone and separate runs, so use the interleaved row for the size of the gain. MiniCPM and Gemma match the desktop ship runs (`x-m-ship`, `x-g-ship`, 23/30) |
| 11-05 | **Two-roll release gate on the applied candidate.** Reference: the ship prompt at 400 tokens (`v3-ref-warm`, `v3-ref-warm2`) | Gated set, `v3-rc-ling`, `v3-rc-ling2`, `report.py --run v3-rc-ling --roll2 v3-rc-ling2 --gate v3-ref-warm,v3-ref-warm2`, binary `72bc8b94639b` | **FAIL on pairwise quality only: pooled test split 30/37/53, p 0.015, mean −0.05.** Canary 24/24, unverified 10.9% against 10.7%, expected 99/106 against 95/106, abstention 52/60 against 45/60. Roll 1: 19/17/24, p 0.54, +0.17. Roll 2: 11/20/29, p 0.006, −0.28. Controls, no-meaning edits against ship: pooled 30/51/39, p 0.34. Travel −0.92 (lost 5 of 6), how-to −0.67 (lost 3 of 6), factual +0.58 | The checks with objective answers improve, and the judge finds a small average loss. A pooled sign test counts each of the 60 questions twice, so its p value is too small |
| 11-05 | **The gate rule after that result.** The user chose to ship the candidate. The gate now takes one pairwise result per question, the mean of its rolls. **The rule changed after this result was seen** | Same runs, test split (60), no new judge calls, binary `72bc8b94639b` | **PASS: 18/19/23, p 0.53, mean −0.05, −0.35 to +0.32.** Correctness +0.05, completeness −0.02, groundedness −0.02, usefulness −0.07. `seed-fac-15` carries +0.13 of the mean; without it −0.19, −0.41 to +0.03. Five how-to questions: mean −0.80, because the candidate read 500 tokens and the reference 400. The other 54: 16/18/20, p 0.62, −0.13, −0.37 to +0.10. Outside the test split, 80 questions (not locked): +0.36, 26 won, 18 lost | The test split must not tune the prompt |
| 11-05 | New how-to prompt: numbered steps, and a source that covers a step is cited at that step | Scratch binary `cd31d984f512`, 29 how-to questions outside the locked split, `x-hb*`, `x-hn*`, `x-hb500d`, `x-hn450d`, `x-hn400d`, `x-hq450`, `x-hq450d`; against the ship how-to prompt at 500 | Citations 1.79, 0.59 → **450: 3.17, 3.41**; 23.5 of 29 answers numbered against 16.5; 18/3/8, p 0.076, mean 0.00 [−0.74, +0.68]. 400: 12/4/13, −0.20 [−0.91, +0.48]. Squeaky hinge −6.75, choking +3.50. Stricter wording: 16/2/11, +0.04 [−0.53, +0.60], hinge −5.0 | **Limit 450.** The rule set before the result rejects the stricter wording: the hinge answer must be right again |
| 11-05 | `w1` at evidence limits 450 and 500 | Outside set, two rolls per arm (`x-n450`, `x-n450d`, `x-n500`, `x-n500d`), against `x-T-base`; 400 is `x-T-400`, `x-T-400d` | 400: 41/56/45, p 0.75, −0.14 [−0.46, +0.18]. 450: new 458, 45/41/56, p 0.32, **−0.16 [−0.51, +0.19]**. 500: new 504, 42/39/61, p 0.08, **−0.19 [−0.56, +0.17]** | **The evidence limit does not move the total score, so 400 stays.** All arms share one reference roll, so its luck is in every number here |
| 11-05 | **Release gate on the frozen candidate:** Ling only, `w1`, evidence 400, new how-to prompt at 450. Reference: `v3-ref-warm`, `v3-ref-warm2`. Only the changed how-to answers went to the judge | Gated set, clean build `d57c4513e73b` (`v4-rc-ling`), second roll through scratch binary `cd31d984f512` (`v4-rc-ling2`), `report.py --run v4-rc-ling --roll2 v4-rc-ling2 --gate v3-ref-warm,v3-ref-warm2` | **PASS.** Canary 24/24, unverified 10.2% against 10.7%, expected 95/106 against 95/106, abstention 52/60 against 45/60, pairwise 20/18/22, p 0.88, **+0.03 [−0.27, +0.40]**; correctness +0.08, completeness −0.04. Citations 3.17, 3.03 against 2.87, 2.70. How-to on the test split +0.17 (before this prompt −0.80). Expected fell from 99 to 95: the hinge fails in both rolls, a real loss | **Not a clean pre-registered result.** I read test answers before this prompt. The numbered-steps idea came from the CPR test answer. The gate takes one result per question only since this same day. On the phone only 2 of the 9 `perf` prompts change, so the interleaved speed result still holds: total 173 → 133 s, 18.4% fewer tokens written |
| 11-05 | **Frozen candidate in the app, and Phonon freed after each answer** | Pixel 10, Ling on the CPU, Starter packs, `tts-en`, debug APK `171c7328b7bd`, battery 28 to 35 °C, five questions, then one short answer read five times, not interleaved | The summary rows have the numbers. Threads 59 → 61, then stable. A past answer read before the first search: total 7.91 s against 8.13 s, same 90 tokens | Phonon's 0.2 GB now returns after each answer, apart from about 60 MB over five readings. `RAYON_NUM_THREADS` no longer limits the search. The leaking-tap answer cites `Hot tapping`, a pipe-welding article, so a higher citation count does not prove that each citation is right |
| 11-06 | Unused code removed from the frozen candidate: decode pacing, the power hint and short answers on a hot phone | Binary `0a72d15f7b05`, `x-v4-final-check` against `v4-rc-ling`, 35 questions of the gated set | 35 of 35 answers identical byte for byte. Debug APK `6f19bdcf1e46` builds; not run on the phone. The hot-phone path does not run on the desktop | The gate result stands |
| 11-06 | **The 0.4.0 search changes on Gemma and MiniCPM:** learned fusion, sentence head and follow-up check, on against off. The rule, set before the run: a model that fails the gate turns them off, with no tuning | Desktop, `3fb99f0`, binary `0a72d15f7b05`, second roll through scratch binary `cd31d984f512` (18 canary and how-to answers per model identical to the first build). Gated set, two rolls per arm: `x-g4-on`, `x-g4-on2`, `x-g4-off`, `x-g4-off2` (Gemma 4 E2B Q4_0, desktop stand-in, `--profile=gemma4-npu`); `x-m4-on`, `x-m4-on2`, `x-m4-off`, `x-m4-off2` (MiniCPM5-2B pure Q4_0). MiniCPM at 400 tokens, one roll: `x-m4-on400`, `x-m4-off400`. 30 locked threads: `x-g4-on-conv`, `x-g4-off-conv`, `x-m4-on-conv`, `x-m4-off-conv`. All arms have the turn-kind retrain | Gemma: **FAIL on expected answers only, 95/106 against 96/106.** Canary 24/24, unverified 4.0% against 4.0%, abstention 45/60 against 45/60, pairwise 32/8/20, p 0.126, +0.41. Threads 13/10/7, p 0.26, +0.42 [−0.43, +1.20]. On both rolls, `seed-fac-15` and `seed-mh-06` gain and `rec-12` and `seed-exp-11` lose the expected answer. Time of 142 questions: 843 and 820 s against 799 and 799 s. MiniCPM: **FAIL on canary 22/24 and abstention 45/60 against 49/60.** `can-11` says that it cannot answer on all three runs with the changes. Unverified 5.4% against 4.6%, expected 98/106 against 95/106, pairwise 23/10/27, p 0.67, −0.14. Threads 11/8/11, p 1.00, −0.62 [−1.73, +0.42]. Time: 578 and 572 s against 616 and 611 s. MiniCPM at 400: **FAIL on canary 11/12 and unverified 6.1% against 3.5%.** Expected 49/53 against 47/53, abstention 22/30 against 23/30, pairwise 19/13/28, p 0.24, −0.28. Time: 494 s against 483 s | **Both models turn the changes off.** Gemma misses by one point and the pairwise result leans to the changes, but that lean is not significant and the changes do not make Gemma faster, so the rule stands. The NPU did not run |
| 11-06 | `ModelProfile::learned_search`: on for Ling only. The evidence limit of 500 for the sentence head on other CPU models goes away | Binary `2a2fb360d258` (`3fb99f0` with the switch), desktop, no judge | MiniCPM: 142 of 142 and 30 of 30 identical to `x-m4-off` and `x-m4-off-conv`. Gemma stand-in: 142 of 142 and 30 of 30 identical to `x-g4-off` and `x-g4-off-conv`. Ling: 142 of 142 identical to `v4-rc-ling`, and 30 of 30 identical to binary `0a72d15f7b05` | The switch does what the arms measured, and Ling's gate result stands. Not run on the phone |
| 11-06 | **The final models on the Wikibooks pack that the app lists.** Runs v3, v4 and `x-*` used the old local build (2026-09-30, 83,521 pages); the catalog lists build 2026-11-02 (83,039 pages, no alternative-medicine books). The rule, set before the run: the gate results stand with no errors, canary as good as before, at least as many expected answers, and no onion advice in `seed-how-12` | Desktop, binary `2a2fb360d258`, gated set, one roll, no judge: `x-n-l` against `v4-rc-ling`, `x-n-m` against `x-m4-off`, `x-n-g` against `x-g4-off` (desktop stand-in) | Ling: 6 of 142 answers change, canary 12/12, expected without canary 48/53 against 47/53, and the onion advice goes away. MiniCPM: 11 change, canary 12/12, expected 47/53 against 47/53. Gemma: 11 change, canary 12/12, expected 48/53 against 48/53. No errors, and no evidence from the removed books. Only `seed-how-12` flips an expected answer, on Ling. The judge did not read the changed answers, which include `abs-01` and `abs-11` on MiniCPM and Gemma | **The gate results stand.** A user keeps the onion advice with Ling until they install the new Reference shelf |
| 11-06 | **Upgrade in place, 0.3.2 to 0.4.0.** The rule, set before the run: install with no uninstall, no crash, the old conversation opens, all three models give the right facts (330 m, 1887), and MiniCPM answers correctly after the old state file and after a fresh one | Pixel 10, `.spike`, debug APK `9f68bd81d3f5` (v0.3.2 with telemetry, `affd9a3`), then `c23f5fcef225` (`ecd4549`), data kept | PASS on all five. Settings kept, crash log empty. First word in 0.4.0: MiniCPM 7.42 s, then 4.75 and 5.5 s; Ling 7.47 s, then 4.78 s; Gemma 2.62 s, then 1.28 s. At the first start the app replaced the MiniCPM state file of 0.3.2 (1,207 tokens) with no logged error; the new file (1,208 tokens) is byte-identical to a fresh 0.4.0 file. Our reading, not checked: the BOS token makes the token check reject the old file | A user who upgrades pays one state rebuild per CPU model. Not run on the signed release APK |
| 11-06 | **Final build against the candidate.** First rule, set before the run: first word ≤6.0 s, prefill ≥84 tok/s, decode ≥12.5 tok/s, else a second pass and a side-by-side run. Second rule, set before the second pair: a regression is more than 15% worse on decode, prefill or first word, on the means of the two pairs with the battery at about 33 °C | Pixel 10, Ling, 9 perf prompts, skin ≤34 °C, `.spike`, debug APK `c23f5fcef225` (`ecd4549`) against `0884e702c25f`, interleaved | First pass, battery 34.9 °C: prefill 117.6, decode 11.8, first word 4.97 s, total 154.7 s; decode missed the first rule. Side by side, candidate → final: prefill 102.9, 93.0 → 90.3, 92.8 tok/s (−6.5%); decode 16.2, 14.1 → 14.9, 14.9 tok/s (−1.7%); first word 4.97, 6.09 → 6.48, 5.43 s (+7.7%); total 123.1, 139.1 → 142.6, 140.6 s (+8.0%); tokens written 1,191 → 1,284 (+7.8%) | No regression by the second rule. The final build's means against the 11-04 ship row, not interleaved: first word 6.83 → 5.96 s (−12.7%), prefill +11.9%, decode +16%, total 173.25 → 141.6 s (−18.3%), tokens 1,460 → 1,284 (−12%). The −23.6% of the summary row belongs to the candidate. One hot-battery pass is not enough to blame the battery for the low decode. The energy rails read zero on many answers, so these passes give no energy number |
| 11-06 | **Gemma and MiniCPM in the app with the search changes off.** The rule, set before the run: no error rows and canary 12/12 on both models | Pixel 10, `.spike`, debug APK `c23f5fcef225` (`ecd4549`, its native libraries match the release APK `70fe599f3c84` from `7ac0a18`, apart from the build stamp in `libcommonplace_ffi.so`), 62 questions, warm phone, `x-inapp-rel-*` against `x-inapp-final-*` (changes on, APK `b12647aa6ec2`, 2026-11-05) | PASS. Gemma, on → off: first word median 1.39 → 1.34 s, total median 4.8 → 4.23 s, decode 24.3 → 25.8 tok/s; canary 12/12 both, recent 17/20 → 18/20, abstention 24/30 → 26/30, invented 0 → 1 (`abs-25`). MiniCPM, on → off: first word 7.85 → 7.78 s, total 16.4 → 15.78 s, decode 7.4 → 7.3 tok/s, prefill 54.0 → 54.1 tok/s; canary 12/12 both, recent 18/20 → 17/20, abstention 23/30 both, invented 3 → 2 (`abs-08`, `abs-21`) | Turning the changes off cost no speed in the app. 62 questions and two runs on different days cannot separate the arms on quality |
| 11-06 | Read aloud on the final build. No rule was set before the run; the check repeats the 11-05 row | Pixel 10, `.spike`, debug APK `c23f5fcef225` (`ecd4549`), Ling, `tts-en`, battery 38.7 °C, five questions, then one short answer read twice | Both readings spoke for 24 s with no error and an empty crash log. RSS 5,309 MB before, peak 5,424 and 5,441 MB, 5,317 and 5,334 MB after each reading (limit: 6 GiB with the app on screen) | Phonon still frees its memory after each answer |
| 11-06 | Where Ling's time goes on the phone, from hardware counters and samples | Pixel 10, `llama-bench` `b040-ship` (build of 2026-11-03, patches 0001-0010, 0012, 0013, so no 0014 or 0015), skin 37-41 °C, depth 1,100, 5 s counters and 6 s samples, one run each. Decode: 4 threads. Prefill: 6 strict threads. `GGML_KLEIDIAI_CHUNK_MULTIPLIER=16` on prefill, one run | Decode 23-24 tok/s: 1.57 instructions per cycle, memory stalls 48% of cycles. KleidiAI 1x4 int8 matmul 35%, Q4_0 GEMV 22%, spin in the thread loop and `ggml_barrier` 11%, attention 6%. Prefill 158-165 tok/s: 3.24 instructions per cycle, memory stalls 7%. Attention GEMM 19%, Q4_0 GEMM 18%, spin 11-14%, Q5_0 5.5%, F32 dot 5%, Q4_1 dot 4.5% (3 tensors). dTLB walks: about 17,000 per decoded token. Multiplier 16: spin 11.2% → 10.2%, 165.5 → 168.0 tok/s, inside the spread of about ±9 | No change to the app. Decode waits on memory and prefill waits on the CPU. The spin share reads as load imbalance, from the addresses of the loop and not from a disassembly |
| 11-06 | MiniCPM on the same profile, and finer KleidiAI chunks on decode | Pixel 10, `b040-ship`, depth 1,100, one profile run per case from skin 31-36 °C; `GGML_KLEIDIAI_CHUNK_MULTIPLIER` 4 and 16, `tg64`, A B B A, 3 repetitions, skin 37-37.5 °C | MiniCPM decode 17.3 tok/s: 1.84 instructions per cycle, memory stalls 37% of cycles, KleidiAI Q4_0 matmul 75%, attention 11%, spin 10%. Prefill 121.6 tok/s: 3.46 instructions per cycle, memory stalls 3%, KleidiAI 60%, attention 29%, spin 8%. Multiplier 4 against 16: MiniCPM 16.4, 13.4 against 12.3, 16.3 tok/s; Ling 21.1, 25.6 against 24.7, 25.8 | No change. Finer chunks show no clear gain: runs of one setting differ by up to 25% |
| 11-06 | **Ling off screen, and the hidden-app fix.** Before: the app died 3 times in 11 minutes with Ling (low memory twice, ANR once), and the crash log stayed empty. The fix: unload Ling when the app goes off screen unless an answer runs, load no model in a process that starts hidden, load again on return | Pixel 10, Private space running. Before: signed APK from `ecd4549`, Home key, then reopen and one question. After: debug `.spike` APK `94d2e9cd341c` (`ecd4549` with the fix, uncommitted), Ling, Home key, reopen and one question; the hidden start and MiniCPM on the same APK | Before: within 8 s of hiding, RSS 5,013 → 2,902 MB, swap 0 → 2,080 MB, free swap 2,753 → 671 MB; no kill of the app in this run, and the answer after reopening took 14 s. After: 2 s after hiding, RSS 2,885 MB with swap 1,697 MB; 5 s after, RSS 327 MB, swap 260 MB, available 1,972 → 6,267 MB. Ling was ready about 5 s after reopening, with the file in the page cache. One answer took 8 s. A process that starts hidden stayed at 281 MB and loaded on return. MiniCPM stayed loaded at 2,190 MB with no swap. One low-memory kill of another app in the Ling run | The 3 GiB limit explains the swap. The 17:25 burst of 20 kills did not repeat, so its trigger stays unproven. The next rows hold the signed APK |
| 11-06 | **Leaving the app during an answer.** The rule, set before the run: RSS above 1 GB 30 s after a hidden answer means the fix is not complete | Pixel 10, `.spike`, one long question, Home key 1.5 to 2.5 s after sending. First build `94d2e9cd341c` with all three models, then debug APK `005492fa7360` (`ecd4549` with the second fix, uncommitted) with Ling | All three models finish while hidden, and the answer is on screen on return. First build: Ling 17.5 s, RSS 3,381 MB and swap 1,805 MB 30 s after the end, so the fix was not complete; Gemma 8.0 s, RSS 1,978 MB; MiniCPM 14.7 s, RSS 2,091 MB. Second fix: Ling 22.2 s, free swap as low as 422 MB during the answer, RSS 461 MB and swap 274 MB at the first sample after the end, 441 MB 15 s later | The second fix unloads Ling when the answer ends. The answer still runs under the 3 GiB limit. One run each, not interleaved |
| 11-06 | **The signed release APK on the Pixel.** Scope set before the run: upgrade, read aloud, and hide and reopen on each model; no speed or quality measure | Pixel 10, Private space running, `app.commonplace`, signed APK `d775acc29fd4` (`7ac0a18`, unsigned `70fe599f3c84`), installed over the earlier 0.4.0 signed build. MiniCPM is not installed there, so it ran on `.spike` `005492fa7360` | Install kept history and settings. Ling: 12 s answer, bundled voice with no error, RSS 5,541 MB at most while reading. Hidden while reading: 575 MB after 7 s, and the voice stopped. Ready about 15 s after return. Hidden during an answer: 297 MB by 16 s after sending. Screen off: 4,719 → 209 MB within 8 s, available 1.9 → 6.3 GB, free swap as low as 346 MB on the way; unlock with no kill. Gemma: 7 s answer, system voice, RSS about 2 GB, stays loaded and ready on return. MiniCPM: 21 s and 12 s answers, RSS about 2 GB, stays loaded. Crash log empty and no kill of the app in every run; 7 kills of other apps in the MiniCPM run, with `.spike` and the main app both loaded | PASS. The upgrade from 0.3.2 did not run on the signed build. One run each |
| 11-06 | Where Ling's memory goes | Tensor sizes in `Ling-3.0-tiny-Q4_0.gguf`, 526 tensors, 4,402 MB. Desktop `commonplace ask`: RSS 5,144 MB. Pixel 10 after load: 4,897 to 5,059 MB | Expert weights 3,738 MB (85%): 128 experts, 8 used at a time. Output rows 189 MB, embeddings 130 MB, other weights 345 MB. The rest of the RSS, about 0.6 GB on the phone, is not split by part | Unused output and embedding rows could save about 120 MB at most. A larger cut needs weights that Android can drop without swap. Not tested |
| 11-06 | **MiniCPM with DSpark, clean phone test.** Go rule from the notes: at least 1.4x faster writing | Pixel 10, `llama-speculative-simple` from b12032 with all patches (master `f0447f7`, test timers and a no-draft switch, uncommitted), official drafter requantized to pure Q4_0 (187 MB), draft length 3, 4 threads, CPUs 2-7 not strict, sampling temp 0.3, top_k 40, top_p 0.9, penalty 1.05, three RAG prompts of about 1,700 tokens, drafter on and off in the same tool, one run per setting from skin 36 °C. Hot: 7 pairs back to back, skin 40-41 °C, two prompts | From 36 °C: 16.5, 16.0, 16.1 → 24.6, 22.5, 26.4 tok/s (×1.50, ×1.41, ×1.64). Per step: draft 18 ms, verify 77 ms, 2.2-2.6 tokens; plain 61 ms per token. Drafter at prefill: 0.3 s of about 16 s. Length 2: 18.9-22.8 tok/s; length 5: 15.0-16.3 tok/s (other tool for plain, 16.5-17.6). Hot: mean 7.6 (5.7-9.7) → 14.1 (13.4-15.2) tok/s, ×1.9. A 200-token run at about 37 s against 51 s. `llama-bench` on the hot phone gives a smaller gap: 70-92 ms for one row, 107-126 ms for four, so about ×1.4 | Passes the go rule on the command line. Needs the app, the eval and a hot session in the app. The hot ×1.9 and the bench's ×1.4 disagree; the app's own hot plain decode (7.5 tok/s) matches the first. Cause open |
| 11-06 | **Repacked weights in a file.** Test patch: the repack and KleidiAI buffers map a file; the first load writes it, later loads skip the conversion | Pixel 10, `llama-completion`, same llama.cpp, command line, not under the app's cgroup limits, one run per setting from skin 36 °C, prompts p00 and p04 | Ling, normal → file: anonymous 4,612 → 200 MB, RSS 4,619 → 4,239 MB, load 11.8 → 0.6 s with the file in the page cache, prefill 197.7 → 195.3 tok/s, decode 17.2 → 24.7 and 22.4 → 18.6 tok/s (p00, p04). After the p04 load at 4.8 s the file was probably partly out of cache. Cold file: load 6.3 s, prefill 145 tok/s, decode 19.8 tok/s. First load: 17.3 s, RSS 6,624 MB. Disk +4.35 GB. MiniCPM: anonymous 1,625 → 274 MB, RSS 1,633 → 1,513 MB, load 1.2 → 0.5-0.6 s, decode 16.7 → 16.7 and 14.9 → 16.3 tok/s, cold load 5.0 s, disk +1.27 GB. MiniCPM + DSpark: anonymous 2,054 → 522 MB, RSS 1,919 MB, decode 23.6, 22.3 → 24.5, 22.2 tok/s, drafter file +163 MB. `MADV_POPULATE_READ` at load: Ling cold load 22.1 s, prefill still 148 tok/s | No visible speed cost when warm; decode runs are too few and too noisy to say more. The prefill slowdown after a cold read has no known cause. Read-ahead as written fails. Untested: the app, the 3 GiB hidden limit, low-memory kills |
| 11-06 | Verify cost with logits on every row, hot and cool | Pixel 10, `llama-bench` with all-row logits, weights in a file, depth 1,100, 4 threads | Cool (skin 36 °C): Ling 41 ms one row, 72-75 ms four rows (×1.8); MiniCPM 55 ms and 69-71 ms (×1.25-1.30). Hot (skin 40-41 °C): Ling 64-65 ms and 115-169 ms (×1.8-2.6), first pair dropped (261 ms, likely paging); MiniCPM 70-92 ms and 107-126 ms (×1.2-1.5) | Ling's verify stays expensive when hot. A Ling drafter at 18 ms would need more than about 2.2 tokens per step to break even on a cool phone. N-gram stays closed |
| 11-06 | Freeing the duplicate weights: hole punching in the original model file after the repacked file exists | Pixel 10, f2fs, test copies in `/data/local/tmp`, `llama-completion` with a test patch, prompt p00, seed 1, one run per setting from skin 36 °C | Disk use, Ling: model file 4,415 → 312 MiB, repacked file 4,150 MiB, total 4,462 MiB against 4,415 today (+47 MiB). MiniCPM: 1,357 → 152 MiB, repacked file 1,208 MiB, total 1,360 MiB (+3 MiB). File sizes on paper stay the same. Answer text byte-identical before and after on both models (55 and 66 tokens). Ling: prefill 196.9 → 201.3 tok/s, decode 21.3 → 22.5 tok/s, load 0.8 → 0.6 s. MiniCPM: prefill 152.2 → 150.0 tok/s, decode 17.5 → 16.2 tok/s, load 1.1 → 0.7 s | The disk cost goes away on these runs. The peak during the first conversion stays. A changed repack layout then needs a new model download. Ling's reduced head (0015) needs its rows kept, about 190 MB, and was not in this test |
| 11-07 | Ling expert routing: can unused experts be dropped? | Desktop, native build of b12032 with all patches (master `f0447f7`, a test counter, uncommitted), `llama-completion`, 10 RAG prompts, 200 tokens, layers 1-22, seed 1 | Near uniform across 128 experts: the top 32 take 28% of the selections, against 25% if uniform | Expert pruning closed |
| 11-07 | Loose draft acceptance on MiniCPM with DSpark: keep a draft token that the target rates at or above a limit (`SP_RELAX`) | Same desktop build, `llama-speculative-simple`, drafter Q4_0, the app's sampling, 10 RAG prompts, 200 tokens, one pass, seed 1 | Draft length 3, tokens per step and rows per step: strict 2.41, 4.00; relax 0.05 2.66, 4.00; 0.1 2.54, 4.00; 0.2 2.44, 4.00; 0.3 2.53, 4.00. `p-min` 0.5: 2.11, 2.71. Relax 0.1, length 4: 2.69, 5.00; length 5: 2.84, 6.00 | No use: the values do not rise or fall with the limit, so the differences read as noise, and the setting changes the text. No speed measured |
| 11-07 | Expert overlap and gate weights on Ling: why a verify batch costs more | Desktop, b12032 with all patches (master `f0447f7`, test trace, uncommitted), `llama-completion`, 4 RAG prompts, 200 tokens at most, 12,014 token-layer records | Distinct experts per layer: 1 row 8.0; 2 rows 12.6; 3 rows 16.5; 4 rows 19.8; 8 rows 30.0 for consecutive tokens (4 random tokens of the same answer: 22.7). Gate weight by rank: 24.6, 18.6, 14.7, 11.9, 9.8, 8.1, 6.8, 5.5% | At 245 MB of experts per token and about 20 GB/s, experts explain about 6 ms of the phone's 10.7 ms per extra row (estimate). MiniCPM has no experts and pays about 5 ms per row. Flat gate weights make it costly in quality to drop experts on draft rows. Not tested |
| 11-07 | **Ling with free DSpark drafters on our prompts.** `jayyun98/Ling-3.0-Tiny-DSpark-50K` (MIT, Q4_0 110 MB) and its blended sibling (licence listed as other, Q4_0 153 MB, test only) | Desktop, same build, `llama-speculative-simple`, target Ling Q4_0, 10 RAG prompts, 200 tokens at most, the app's sampling, one pass. The drafter card's chat run was 20 prompts on a GPU, so 5 chat prompts here are only a rough check | Tokens per step at draft length 3: Q4_0 1.71 (survival by position 0.47, 0.18, 0.07), BF16 1.71, greedy 1.80, blended 1.85 (0.52, 0.23, 0.09). Length 7: 1.77. Confidence gate 0.3: 1.66 at 2.68 rows; 0.5: 1.53 at 2.00 rows. Chat at temperature 0, length 7: 2.08, blended 2.11, card 2.68. Rollback ran without failure; correct text was not checked. Layer ids match the converter's rule, so no bug found. `Ling3UBTreeModel` does not convert | Below the break-even of about 2.2 tokens per step. Estimate for a cool phone, from 41 ms plain, 10.7 ms per row and 18 ms draft: ×0.77 to ×0.90, so Ling would be slower. Ling's drafter was not timed on the phone |

Known weak spots that these experiments did not fix:

- Questions about who held an office at a time: Ling answers "Donald Trump" for 2016 and "Vladimir Putin" for 2010 next to the correct term dates. Thinking mode fixed 1 of 4 such answers.
- Title traps: the reranker ranks the colour article "Sky blue" first for "Why is the sky blue?" (fp32 and int8 alike).

## Not measured yet

- What set off the burst of 20 low-memory kills with Ling on screen (2026-11-06). It did not repeat in the tests after it.
- The app's memory outside the model weights, by part.
- The upgrade from 0.3.2 on a signed release APK, and MiniCPM on one. `.spike` builds ran them.
- The time of the underline check (rarity lookups) on the Pixel.
- The time of the Wikidata second hop on its own.
- Prefill with pinned threads and with `n_ubatch` 1024 against 512 in the app.
- Voice input with a real voice on the phone's microphone: transcript quality, stop-to-final latency, heat.
- Gemma on the NPU on a hot phone (thermal headroom above 0.85). A long session is measured in [Long sessions and heat](#long-sessions-and-heat).
- MiniCPM decode at depth 0 on a cool phone, with the candidate's llama.cpp.
- The phone speed of patches 0005 to 0010 and 0013 alone.
