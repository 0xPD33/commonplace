# Benchmarks

This file logs every measurement with the device, the build and the date. Older entries stay as history. Each entry says which library it used.
The acceptance device is a Pixel 10 on GrapheneOS. The desktop and emulator rows check that the code paths work. They say nothing about phone speed.

Build: working tree before the first commit (`CP_BUILD=dev`), llama.cpp `b11240`.

## Current numbers (2026-10-02)

Pixel 10 (Tensor G5, GrapheneOS on Android 17, 11.5 GB MemTotal), Ling-3.0-tiny Q4_0, the 2026 `enwiki-core`, `wikidata-facts` and the breadth packs. The reranker is `model_nb8.onnx` on the top 24 passages. The question rewrite is off (the default). Details are in the sections below.

| Measure | Value |
|---|---|
| Card (search, rerank, instant answer) | Median 0.91 s |
| Rerank of 24 passages | Median 424 ms |
| First answer word | About 7 s (median 7.0 s with the rewrite on, 7.6 s with it off) |
| Ling decode in the app | About 18 tok/s on a cool phone. 8-11 tok/s after the phone passes about 37 °C. |
| `llama-bench` Ling, 6 big cores | Prefill 131.6 tok/s (pp512), decode 25.5 tok/s (tg64) |
| Peak RSS with Ling | 5.1 GB. The memory limit is 6 GiB with the app on screen and 3 GiB when it is hidden. |
| Voice input | +0.58 GB PSS while the mic is on |

Desktop, 63 seed questions, Claude as the judge: score ratio 0.521 against Claude with web search (`v17-norewrite`, [EVAL.md](EVAL.md)). Multi-turn threads, measured on 2026-09-30: 0.616 on the 60 tuning threads and 0.614 on the 30 locked test threads (`cv2-dev-base`, `cv2-test-base`).
Desktop, NQ-open retrieval with the 2026 Wikipedia (2,610 held-out questions): hit@5 0.738, hit@10 0.780, pool recall 0.825.

## Desktop CLI — AMD Ryzen 9 9950X3D, 62 GB RAM, 8 llama.cpp threads, AVX2 build (2026-09-29)

| Model | Query | Card | Prefill | Decode | TTFT | Notes |
|---|---|---|---|---|---|---|
| LFM2.5-1.2B Q4_0 | "capital of Malta and how many people" | 164 ms | 539 tok/s | 87.9 tok/s | 2.3 s | first query, prefix not cached, ~825 evidence tokens |
| LFM2.5-8B-A1B Q4_0 | same | 176 ms | 296 tok/s | 58.3 tok/s | 3.9 s | first query, thinking off |
| LFM2.5-8B-A1B Q4_0 | "Compare the Nile and the Amazon rivers" | 176 ms | 297 tok/s | 56.8 tok/s | 9.2 s | planner + compute + synthesis |

The 2023 English Wikipedia pack (`enwiki`, 41.5M passages, 12.8 GB; the 2026 pack of 44.6M passages replaced it on 2026-10-02) with `wikidata-facts`, LFM2.5-8B-A1B Q4_0, five questions from the in-app benchmark list (`commonplace bench`):

| Metric | p50 | p90 |
|---|---|---|
| Card | 230 ms | 235 ms |
| First answer token | 4.9 s | 9.0 s |
| Full answer | 7.0 s | 11.5 s |
| Decode | 58.7 tok/s | — |

Retrieval breakdown on that 2023 pack: BM25 over 41.5M passages 30–50 ms, leaf-mt encode 2 ms, binary IVF search (nprobe 48) 6–8 ms, rerank of 40 passages 110–175 ms.

Retrieval on the `simplewiki` pack (keyword search only, 718k passages): sparse 2 ms, rerank of 40 passages with Ettin-17m fp32 on 4 threads 163 ms, card 146–197 ms.

Build trap check: the Nix C compiler wrapper strips `-march=native`. With `GGML_NATIVE=ON` llama.cpp reported no AVX and prefill was 103 tok/s. With explicit `GGML_AVX2/FMA/F16C/BMI2` it is 539 tok/s. The Android arm64 build passes `GGML_CPU_ARM_ARCH=armv8.2-a+dotprod+i8mm`, and the app checks the llama.cpp system-info string for `DOTPROD` and `MATMUL_INT8` (Diagnostics screen).

## Android emulator — x86_64 AOSP API 36 image, 6 vCPU, 8 GB, KVM (2026-09-29)

The app ran with the `simplewiki` pack and LFM2.5-1.2B Q4_0 (4 threads). The x86_64 emulator uses the host CPU through KVM, so these numbers are not phone numbers.

| Query | Card | TTFT | Decode | Total | Notes |
|---|---|---|---|---|---|
| "Compare the Nile and the Amazon" | < 1 s | — | — | 13.2 s | planner used |
| follow-up "Which one is longer?" | 688 ms | 5.7 s | 57.4 tok/s | 10.2 s | system prefix restored from the cache |
| "What causes the northern lights?" on the 2023 `enwiki-core` (7.2M passages) | 525 ms | 13.3 s | 54.5 tok/s | 16.3 s | planner used, first question after model load |

Source: `scripts/e2e_emulator.py` runs under `artifacts/e2e/` (screenshots + `report.json`).

## Pixel 10 — Tensor G5, GrapheneOS on Android 17, 11.5 GB MemTotal, 128 GB UFS 3.1 (2026-09-29)

Cores as the kernel reports them: 2 small (capacity 207), 5 medium (824), 1 big (1024).

`llama-bench`, LFM2.5-8B-A1B Q4_0, pinned to the 6 big cores, cold phone:

| Setting | Prefill pp512 | Decode tg64 |
|---|---|---|
| Repacked weights, 4 threads | 102 tok/s | **25.0 tok/s** |
| Repacked weights, 6 threads | **120 tok/s** | 17.1 tok/s |
| Plain weights (no repack), 6 threads | 51 tok/s | 16.0 tok/s |
| Warm phone (after several minutes), repacked, 4 threads | 58–66 tok/s | 15.5–17.8 tok/s |
| 6 repeated decode runs, repacked, 4 threads, shell user | — | 25.0 tok/s, no swap |

The app, the 2023 `enwiki-core` + `wikidata-facts`, LFM2.5-8B-A1B:

| Build | Questions | Card | First word | Decode | Notes |
|---|---|---|---|---|---|
| First build | "Why is the sky blue?" | 0.83 s | 22.2 s | 10 tok/s | planner ran; 6.4 GB RAM |
| Second build | 1–2 | 0.56–0.63 s | **6.1–8.5 s** | **22–23 tok/s** | app on screen; 5.4 GB RAM |
| Second build | 3–5 | 0.65–1.1 s | 12–63 s | 5–6 tok/s | app not visible; 3 GiB memory limit |

Card breakdown: query encode 65 ms, keyword 83 ms, dense 72 ms, rerank 410–610 ms.

Memory:

- mmap plus weight repacking peaked at 7.0 GB, and Android killed background apps. Reading the model into RAM (`LLAMA_LOAD_MODE_NONE`) peaks at 4.9 GB at the same speed.
- When the app is not visible, Android 17 sets its cgroup `memory.high` and `memory.swap.max` to 3 GiB. The log shows `MemoryLimiter: onLimitExceeded … type=memory.high memHigh=3221225472 … pkg=app.commonplace`. Above the limit, the kernel swaps the model out and decode falls to 5 tok/s.

Source: `artifacts/pixel/llama-bench-8b.md`, `artifacts/pixel/telemetry*.jsonl`.

### Memory limit with the app on screen (2026-09-29, second build)

Screen kept on (`svc power stayon usb`), phone on USB power, 5 questions in a row, cgroup sampled every 2–4 s:

- `memory.high` is **6 GiB** (6,442,450,944) while the app is visible, 3 GiB when it is not. `swapHigh` is 3 GiB in both states.
- The limiter fired once, during model load: `MemoryLimiter: onLimitExceeded … memHigh=6442450944 swapHigh=3221225472`. It then raised `memory.high` to 6,547,308,544. `lowmemorykiller` killed 6 background apps in that second.
- After that, `memory.current` stayed at 6.3–6.5 GB (the cgroup counts page cache from the packs) and swap stayed at 29–38 MB. The model was not swapped out. RSS was 5.5 GB.
- The build still uses 4 threads for both prefill and decode (the split is not in this APK).

| Question | Card | First word | Total | Prompt tokens | Prefill | Decode | Planner | Thermal headroom |
|---|---|---|---|---|---|---|---|---|
| Why is the sky blue? (first after load) | 981 ms | 9.3 s | 20.3 s | 749 (0 cached) | 90 tok/s | 18.4 tok/s | no | 0.57 |
| How do vaccines train the immune system? | 609 ms | 5.4 s | 14.5 s | 666 | 139 tok/s | 18.7 tok/s | no | 0.69 |
| What caused the fall of the Western Roman Empire? | 683 ms | 6.7 s | 21.3 s | 738 | 123 tok/s | 11.7 tok/s | no | 0.70 |
| Compare the Nile and the Amazon | 833 ms | 40.2 s | 60.4 s | 744 | 79 tok/s | 10.6 tok/s | yes | 0.79 |
| Population density of Malta | 912 ms | 46.7 s | 48.1 s | 810 | 70 tok/s | 11.4 tok/s | yes | 0.83 |

- Battery temperature rose from 31.4 °C to 38.1 °C over the 5 questions. Decode fell from 18.4 to 11–12 tok/s.
- On the two slow questions, the planner took about 19 s (prefill 5–7 s, 55–73 generated tokens in 11 s) and returned no subqueries. The compute call then prefilled the evidence again (592–666 tokens, 10–13 s).

Source: `artifacts/pixel/onscreen-memory-20260929.log`, `artifacts/pixel/telemetry-onscreen-20260929.jsonl`, `artifacts/pixel/onscreen-logcat-20260929.txt`.

### `llama-bench`, Ling-3.0-tiny Q4_0 (2026-09-29)

Same settings as LFM2.5-8B-A1B above: big cores (`-C 0xFC --cpu-strict 1`), repacked, loaded into RAM (`-lm none`), 3 repetitions, phone at 33 °C at the start and 38.6 °C at the end.

| Threads | Prefill pp512 | Decode tg64 |
|---|---|---|
| 4 | 95.0 ± 21.6 tok/s | 23.1 tok/s |
| 6 | **131.6 tok/s** | **25.5 tok/s** |

Ling beats LFM2.5-8B-A1B on both (LFM: 119.7 prefill at 6 threads, 25.0 decode at 4 threads). Unlike LFM, Ling decodes fastest at 6 threads. Source: `artifacts/pixel/llama-bench-ling.md`.

### App on screen, new router and thread fix (2026-09-29, third build)

Same 5 questions, screen on, USB power, phone at 34 °C at the start and ~38.5 °C at the end. No planner or LLM compute call ran.

| Model | First word (q1 → q5) | Comparison question, first word | Decode (q1 → q5) | Peak RSS | Limiter events |
|---|---|---|---|---|---|
| LFM2.5-8B-A1B | 9.6, 5.6, 11.4, 11.9, 10.0 s | 11.9 s (was 40.2 s) | 18.1 → 10.3–11.6 tok/s | 5.5 GB | — |
| Ling-3.0-tiny | 10.5, 5.7, 14.3, 15.2, 15.4 s | 15.2 s | 17.2 → 8.3–10.7 tok/s | 5.1 GB | 0 |

Decode falls once the phone passes ~37 °C. During decode only 3–4 app threads are busy, so the idle prefill pool does not compete for cores. Source: `artifacts/pixel/telemetry-onscreen-v2-lfm.jsonl`, `artifacts/pixel/telemetry-onscreen-v2-ling.jsonl`.

### `llama-bench`, Maple-Preview TQ1_0 (20B, ~1B active; 2026-09-29)

Same settings as Ling (6 big cores, repacked, in RAM, 3 repetitions); phone at 34 °C at the start and 37.3 °C at the end.

| Threads | Prefill pp512 | Decode tg64 |
|---|---|---|
| 4 | 69.7 tok/s | 37.9 tok/s |
| 6 | 104.0 tok/s | **42.1 tok/s** |

Maple decodes 1.65× faster than Ling but prefills 21% slower. On the 63 seeds it scores 0.40 against Ling's 0.44 (factual 0.32 vs 0.58), and its answers are shorter (median 70 vs 104 tokens). Desktop RSS 5.9 GB. Source: `artifacts/pixel/llama-bench-maple.md`, run `seeds-maple`.

### Thinking mode, featured snippet, LiteRT-LM (2026-09-29, Ling build)

- **Thinking mode**, Ling, "Why is the sky blue and not violet?": 384 reasoning tokens (the cap) took 34 s at 14 tok/s; first answer word at 35.1 s, total 46.2 s. The default cap is now 256 tokens (~18 s). Desktop: 7 s of reasoning, first word at 9.1 s.
- **Featured snippet**: the int8 DeBERTa-v3-xsmall reader on the top 3 passages takes 234 ms on the Pixel ("Canberra", margin 15.6). Desktop: 23–59 ms.
- **Tensor G5 NPU**: blocked. GrapheneOS exposes the Edge TPU libraries to apps (`libedgetpu_litert.so` is a vendor public library), and no Play Services are needed. But `litertlm-android` 0.17.1 (the newest on Maven) rejects the LiteRT v2.1.6 dispatch library ("Unsupported dispatch runtime version") and crashes inside the v2.2.0 one (SIGSEGV in `libLiteRtDispatch_GoogleTensor.so`). Without a dispatch library, the G5 `.litertlm` file cannot run at all: "Input tensor not found" on the CPU.
- **Gemma 4 E2B on LiteRT-LM CPU** (generic `.litertlm`), phone at 37 °C, same questions as the Ling runs:

| Question | First word | Prefill (~800 tokens) | Decode | Peak RSS |
|---|---|---|---|---|
| Why is the sky blue? | 5.6 s | 4.8 s | 10.5 tok/s | 2.76 GB |
| How do vaccines train the immune system? | 4.5 s | 4.0 s | 9.5 tok/s | 2.92 GB |

  Gemma's RSS stays under the 3 GiB limit for hidden apps. Its quality is not measured yet: LiteRT-LM does not run in the desktop eval. With the NPU backend selected and no dispatch library, LiteRT fell back to a slower path (13.6 s prefill, 8–9 tok/s, 5.2 GB), so the app now sends generic files straight to the CPU backend.

Source: `artifacts/pixel/telemetry-think-featured-gemma.jsonl`.

## Desktop: routing without the LLM planner (2026-09-29)

Ten fixed questions (lookups, explanations, comparisons, a density, a unit conversion), `serve-eval`, the 2023 full `enwiki`, 8 threads. "Before" is the older desktop binary, which ran the planner on every question except simple lookups.

| Setup | Planner calls | LLM compute calls | First word, median | Total, median |
|---|---|---|---|---|
| LFM2.5-8B-A1B, before | 7 of 10 | 7 of 10 | 8.9 s | 10.9 s |
| LFM2.5-8B-A1B, after | 0 | 1 | 3.0 s | 5.0 s |
| Ling-3.0-tiny, after | 0 | 1 | 2.9 s | 4.8 s |

Desktop prefill: Ling ~440 tok/s, LFM ~370 tok/s. Decode: Ling ~51 tok/s, LFM ~56 tok/s.

## Desktop: voice input, Moonshine Medium Streaming EN (2026-09-29)

`scripts/e2e_stt.py` streams `eval/stt/two_cities_16k.wav` (44 s, LibriVox reading) in 100 ms chunks at real time. Ryzen 9 9950X3D, `moonshine-voice` 0.1.5 on CPU, while another session used about 7 cores. Build: working tree before the first commit.

| Metric | Result |
|---|---|
| WER on the clip | 2.5–4.2% over four runs (two to three word errors; "direct the other way" → "to act the other way") |
| Model load | 0.23–0.35 s |
| First live text | 1.8 s after audio start |
| Stop in the middle of a sentence | final text after 0.33–0.46 s |
| Silence + faint noise (8 s) | no text |
| Memory | 38 MB before load, 750 MB after load, 830–925 MB peak (model files are 257 MB) |
| Mean `add_audio` call per 100 ms chunk | 60–72 ms (max 830 ms, when the 0.5 s update runs) |

Moonshine Small Streaming on the same clip: 136 MB on disk, 429 MB after load, 565 MB peak, about the same words.

Not measured yet: the Pixel 10 (memory next to Ling, stop-to-final latency, heat), Moonshine on the app's ONNX Runtime 1.30.0, key-term biasing.

## Pixel 10: voice input loads (2026-09-29)

Debug build with Moonshine Medium Streaming installed by `scripts/dev-push.sh stt`. The app already held its library and model (5.12 GB PSS). Values are `dumpsys meminfo` PSS.

| State | App PSS |
|---|---|
| Before the mic tap | 5.12 GB |
| Mic on, Moonshine streaming (update every 0.5 s, no errors) | 5.70 GB (+0.58 GB) |
| After the stop tap | 5.16 GB (model freed) |

Not measured yet: transcript quality with a real voice, stop-to-final latency, CPU and heat. The desktop speakers did not reach the phone mic, so the field stayed empty.

## Desktop: reranker quality on NQ-open (2026-09-30)

Retrieval benchmark: the 3,610 NQ-open validation questions (Google queries with Wikipedia answer strings, CC BY-SA 3.0). A question is a hit at k when a normalized answer string occurs in one of the top k passages (the DPR measure). `commonplace pool` gives the fused pool of each question from `data/library` as of 2026-09-30 00:10 (before any new dense codes); `rerank hitk` reorders it. Questions 1000+ only (2,610), because reranker training selects checkpoints on the first 1,000.

| Order | hit@1 | hit@3 | hit@5 | hit@10 | hit@40 (pool) |
|---|---|---|---|---|---|
| Fusion (BM25 + dense + entity + prior) | 0.248 | 0.484 | 0.577 | 0.681 | 0.780 |
| Installed reranker (Ettin-17m, fp32) | 0.487 | 0.632 | 0.680 | 0.733 | 0.782 |

The same 2,610 questions as the 2023 library gained data on 2026-09-30 (installed reranker, fp32). Nine breadth packs and more QIDs raised hit@5 from 0.680 to 0.684. Dense codes for 6.59M `enwiki-extra` passages raised it to 0.692. Dense codes for the 6.07M `enwiki` passages that the prebuilt index missed raised it to 0.700 (hit@1 0.487 to 0.508, pool 0.782 to 0.806). The 2026 Wikipedia raised hit@5 further, to 0.738 (see the seeds section below). The 2023 packs are deleted.

Teacher candidates on 400 of those questions (512 tokens for bge and the 1b, 256 for the others; speed while the GPU also embedded):

| Reranker | hit@1 | hit@5 | Pairs/s on the 5060 Ti |
|---|---|---|---|
| Installed Ettin-17m | 0.495 | 0.685 | — |
| `BAAI/bge-reranker-v2-m3` | 0.512 | 0.693 | 113 |
| `cross-encoder/ettin-reranker-1b-v1` | 0.535 | 0.713 | 38 |
| `cross-encoder/ettin-reranker-400m-v1` | 0.520 | 0.720 | 82 |
| `cross-encoder/ettin-reranker-150m-v1` | 0.522 | 0.710 | 183 |

**The phone's int8 reranker loses about 6 points of hit@1.** Same 17m model, 300 questions, ONNX Runtime 1.30 on the desktop CPU (4 threads, batches of 8 as in the Rust core):

| ONNX file | hit@1 | Spearman vs fp32 | ms per pair |
|---|---|---|---|
| `model.onnx` (fp32; desktop CLI) | 0.523 | 1 | 4.6 |
| `model_qint8_arm64.onnx` (the Android app loaded it until 2026-10-02) | 0.457 | 0.933 | 2.8 |
| Dynamic int8, weight MatMuls only | 0.477 | 0.940 | 3.8 |
| `model_nb8.onnx` (the Android app loads it now): MatMulNBits, 8-bit weights in blocks of 32, accuracy level 4 | 0.517 | 0.9998 | 5.2 |
| MatMulNBits, 4-bit | 0.493 | 0.986 | 5.4 |
| Static int8 (QDQ, weight MatMuls, MinMax calibration on 256 training pairs) | 0.467 | 0.922 | 4.2 |

**Distilled student (`data/models/ettin-17m-cp1`).** Ettin-17m fine-tuned on 16,000 of our own pools (NQ-open train questions and Stack Exchange titles), listwise KL against `ettin-reranker-150m-v1` scores, 4,000 steps of 8 queries × 16 passages, lr 3e-5, 15 minutes on the 5060 Ti. On the 2,610 held-out questions (pools from the library of 2026-09-30 02:00, with the new breadth packs):

| Order | hit@1 | hit@3 | hit@5 | hit@10 |
|---|---|---|---|---|
| Installed Ettin-17m | 0.495 | 0.638 | 0.684 | 0.736 |
| Distilled cp1 | 0.503 | 0.641 | 0.687 | 0.736 |
| `cross-encoder/ettin-reranker-32m-v1` (2× the 17m's cost) | 0.511 | 0.649 | 0.695 | 0.743 |
| `cross-encoder/ettin-reranker-68m-v1` (~4× the cost) | 0.523 | 0.657 | 0.698 | 0.745 |

The gain is small (a third of the gap to the teacher), so the app and the CLI keep the installed model. More pools and epochs may help: dev hit@1 still rose at the last step.

Dynamic quantization also quantizes the activations per tensor, and ModernBERT's activation outliers do not survive that. Weight-only 8-bit keeps fp32 quality. On x86 it is not faster. The Pixel speed is in the next section.

## Desktop: judged seeds (2026-09-30 to 2026-10-02)

63 seed questions, Ling-3.0-tiny Q4_0, Claude judge (both orders), desktop CLI. One run's noise is about +-0.05. Every run is a row in `eval/history.csv`. The first row is `v9-rewrite` (2026-09-29): 0.446 on the 2023 library.

On the 2023 library, the score ratio moved between 0.434 and 0.479 while I added packs and dense codes (`v10` to `v12b`). An Ettin-68m reranker gave 0.486 (`v13-rerank68m`) at a card time of 3.9 s. The 68m model is too slow for the phone.

The table shows the runs with the current library design. The run directories are in `eval/runs/`.

| Run | Change | Score ratio | Correct. | Compl. | Grounded | recall@10 | Card p50 |
|---|---|---|---|---|---|---|---|
| v15-live-all-dense | 2023 Wikipedia, every breadth pack with dense codes (2026-09-30) | 0.464 | 2.26 | 1.22 | 0.92 | 0.174 | 0.84 s |
| v16-enwiki2026 | 2026-09-01 Wikipedia (`wikipedia.py`) replaces `enwiki` and `enwiki-extra` (rewrite on) | 0.508 | 2.40 | 1.24 | 1.06 | 0.223 | 0.84 s |
| **v17-norewrite** | same library, question rewrite off | **0.521** | **2.43** | **1.31** | **1.08** | 0.223 | 0.40 s |

The 2026 Wikipedia is the largest gain. Out-of-corpus questions rose from 0.33 to 0.65 (2023 text could not answer them), explanation from 0.41 to 0.52 and comparison from 0.37 to 0.45. On NQ (2,610 held-out questions) the 2026 Wikipedia raised hit@5 from 0.697 to 0.738, hit@10 from 0.750 to 0.780 and pool recall from 0.805 to 0.825.

The v17 decode speed (34.9 tok/s median) is lower than in older runs (about 52 tok/s). I did not confirm the cause. A busy desktop CPU is likely.

## Pixel 10: reranker files (2026-10-02)

The debug app with 17 knowledge packs (2026 `enwiki-core`, `wikidata-facts`, all breadth packs) and Ling-3.0-tiny, plugged in over USB. Each file was copied in place of the app's reranker and timed with the in-app benchmark (5 questions); `rerank_ms` comes from `files/telemetry.jsonl`, scaled to 40 passages. The phone warmed up during the run: the same int8 file took 509 ms first and 787 ms last.

| File | 40-passage rerank | Thermal headroom |
|---|---|---|
| `model_qint8_arm64.onnx` (shipped until now), first round | 509 ms | 0.68 |
| `model_nb8.onnx` | 1,185 ms | 0.78 |
| `model.onnx` (fp32) | 1,410 ms | 0.79 |
| `model_qint8_arm64.onnx`, last round | 787 ms | 0.82 |

On 400 held-out NQ questions with today's library (desktop CPU): int8 on 40 fused passages hit@1 0.485 / hit@5 0.708; nb8 on 40: 0.510 / 0.713; **nb8 on 24: 0.510 / 0.710** at about 24/40 × 1,185 ≈ 710 ms on the phone, less than int8 on 40. The app now loads `model_nb8.onnx` and reranks the top 24 fused passages (`commonplace-ffi` sets `fuse_keep = 24`; the desktop CLI keeps 40). Card breakdown with 17 packs: sparse ~100 ms, dense ~260 ms (15 dense indexes), fuse ~75 ms.

New debug build (nb8, 24 passages), in-app benchmark, thermal headroom up to 0.84: rerank median 424 ms (392–851), sparse 82 ms, dense 253 ms, card median 2.0 s (1.6–5.1 s), first word median 7.0 s (5.9–10.9 s). Retrieval takes ~0.85 s of the card; the question-rewrite LLM step takes most of the rest, and comparisons ("Compare lions and tigers", 5.1 s) run several retrievals.

**Without the question rewrite** (same build, preference `rewrite = false`, thermal headroom 0.87): card median 1.03 s (0.89–1.10 s; comparisons 0.89 s instead of 5.1 s), first word median 7.6 s. Judged seeds on the desktop: `v17-norewrite` 0.521 vs `v16-enwiki2026` 0.508 with the rewrite (within noise); desktop card p50 0.40 s vs 0.84 s. The three follow-up seeds score the same either way: without the rewrite the engine prefixes the previous turn's topic ("Canberra Why isn't it Sydney?"). The seeds hold almost no misspelled questions, which is where the rewrite should help most.

A later build, with the rewrite preference off by default: card median 0.91 s on the Pixel.

## To measure on the Pixel 10
- Voice input: install the debug build, `scripts/dev-push.sh stt`, then measure RSS with the mic on and off, stop-to-final latency, and CPU while Ling is idle.
- `llama-bench` for Ling-3.0-tiny (Q4_0, Q4_K_M) at pp256/pp512/pp1024/tg128 with 4, 5 and 6 pinned threads, cold and after 5 minutes of load.
- LiteRT-LM Gemma 4 E2B on the Tensor G5 NPU under GrapheneOS (blocked, see above), and its quality on LiteRT CPU.
- Query encode and 40-pair rerank latency in isolation.
- The in-app benchmark (Diagnostics → Run benchmark) with the phone plugged in and awake, and the E2E script with `--push enwiki-core lfm25-8b-a1b`.
