# Models

Every model that Commonplace uses, with its pinned revision and file hash. The model packs ship their license text and credit as `NOTICE.txt` (`pipeline/notices/`), because MIT and Apache-2.0 require the text to travel with the weights. The licenses of the models differ from the license of the code.
`scripts/fetch-models.sh` downloads these exact revisions into `data/models/` and writes `data/models/SHA256SUMS`.

## On the phone

| Role | Hugging Face repo @ revision | File | SHA-256 | License |
|---|---|---|---|---|
| Tested, not used (the fallback model until 2026-10-10; `scripts/fetch-models.sh llm-lfm`) | `LiquidAI/LFM2.5-8B-A1B-GGUF` @ `49c14831707011e64d70b2ebd8462ba08d608434` | `LFM2.5-8B-A1B-Q4_0.gguf` (4.84 GB) | `48ed1465d761311b2fd57b7fb46cf969a20b3a8281945b04e10f52bd1609e715` | LFM Open License v1.0 |
| Main model (CPU, pack `ling3-tiny`; the app reads it into RAM) | `bartowski/Ling-3.0-tiny-GGUF` @ `ea072726af0d2e8ba325b2f90fc0efa762105a91` | `Ling-3.0-tiny-Q4_0.gguf` (4.62 GB) | `c1a548fd60cfb5a46d6e5fd7635224dec473664c9320a656132106bf7ce23cfb` | MIT (model card of `inclusionAI/Ling-3.0-tiny`; the pack ships the MIT text) |
| Small model (pack `minicpm5-2b`; phones with less than 10 GB of RAM use it by default) | `0xPD33/MiniCPM5-2B-pureQ4_0-GGUF` @ `76674264cc1a1b11939b0e6875571d4aae84cb18` (our Q4_0 of `openbmb/MiniCPM5-2B`, see the notes) | `MiniCPM5-2B-pureQ4_0.gguf` (1.42 GB) | `d998b9433fe6e9b20e0dde7780e6c435a8a9cd893538ea70cbf1e09ce28cd5ae` | Apache-2.0 |
| Tested, not used (the small model until 2026-10-19) | `LiquidAI/LFM2.5-1.2B-Instruct-GGUF` @ `8ed288026e23958ad9dfa92d53ed773a8eee7125` | `LFM2.5-1.2B-Instruct-Q4_0.gguf` (0.70 GB) | `2ea801949d760cdf1a2cc04a54262c22c3c0c54f0769d57760c9adeb0e59233f` | LFM Open License v1.0 (commercial use only below US$10M annual revenue) |
| LiteRT-LM model (TPU engine setting; runs on LiteRT's CPU backend) | `litert-community/gemma-4-E2B-it-litert-lm` @ `b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1` | `gemma-4-E2B-it.litertlm` (2.59 GB) | `181938105e0eefd105961417e8da75903eacda102c4fce9ce90f50b97139a63c` | Apache-2.0 |
| Tensor G5 NPU build (blocked: see BENCHMARKS.md) | same repo @ same revision | `gemma-4-E2B-it_Google_Tensor_G5.litertlm` (3.11 GB) | `af1082986639ecde7db95d91be6fe54f8b6b458104734c5bafc204e69d6852dc` | Apache-2.0 |
| Tested, not used (1B active of 20B) | `deepgrove/maple-preview-GGUF` @ `f5466f918e0c50cdb9d4d47a6f35813509a42a30` | `maple-preview-TQ1_0-head-Q4_K.gguf` (4.98 GB) | `54016e4d543bd688829e67103fc85b8396db94b7f8eb3f81fa95884e44393872` | MIT |
| Tested, not used (`fetch-models.sh` no longer gets it) | `bartowski/Qwen_Qwen3.6-35B-A3B-GGUF` @ `5c2410d71524f4f72b023ce8daf7a80528226d5f` | `Qwen_Qwen3.6-35B-A3B-IQ3_XXS.gguf` (15.8 GB) | `4eb30af2df0dde76fe8d0a60f0e88f285ee668dcb1b8da892ee5c13dd6b00399` | Apache-2.0 |
| Query embedder | `MongoDB/mdbr-leaf-mt` @ `1ed41b22ce166d66c24f88ebfc340e1f03adb20f` | `onnx/model_quantized.onnx` + `.onnx_data` (APK); `onnx/model.onnx` (desktop) | `2a3541f3…d8855` + `65dc11da…fce907`; `a3baf432…6a5` + `d23ff906…ded` | Apache-2.0 |
| Reranker (Ettin-17m distilled from Ettin-400m on our own search pools, see notes) | `0xPD33/ettin-reranker-17m-commonplace` @ `ebc0d6c8afdce3af49e12c6fd0b7cc6a6c398769` | `onnx/model_sel8.onnx` (phone: dynamic int8 per channel, except `/layers.0/mlp/Wo/MatMul` in fp32), `onnx/model.onnx` (desktop), `onnx/model_nb8.onnx` (8-bit weights only), `onnx/model_quint8_avx2.onnx` (emulator), head `2_Dense`, `3_LayerNorm`, `4_Dense` | `5ab82f79…41349de`, `aca0130b…f29886`, `f2779800…77ce52`, `0ca6e421…964f22` | Apache-2.0 |
| Reranker base (the app used it until 2026-10-13; `fetch-models.sh` no longer gets it) | `cross-encoder/ettin-reranker-17m-v1` @ `9e4aa35321a6dd1a43ca313f500c4b4f7cfb5cc6` | `onnx/model.onnx`, `onnx/model_qint8_arm64.onnx`, `onnx/model_quint8_avx2.onnx`, same head | `b485f6bf…ed903`, `3f567dda…0cc02`, `2a375cce…6211` | Apache-2.0 |
| Extractive reader (featured snippet) | `tomasmcm/nlpconnect-deberta-v3-xsmall-squad2-onnx` @ `96a6338a770f28b36ace1915a3c22685534ffe1b` (ONNX export of `nlpconnect/deberta-v3-xsmall-squad2`) | `onnx/model_int8.onnx` (90.5 MB) + `tokenizer.json` | `5959dfed4c2ef73027db026732669a2ca983e83b983b472f7423448ff6e1eb7e` | Apache-2.0 |
| Voice input (speech to text) | Moonshine Medium Streaming EN, 245M parameters, `download.moonshine.ai/model/medium-streaming-en/quantized_26_08_21` | 8 `.ort` files + `streaming_config.json` + `tokenizer.bin` (257 MB; `scripts/fetch-models.sh stt`, hashes in `data/models/SHA256SUMS`) | `12915e76…0c37` (`encoder.ort`), `193bb366…9486` (`decoder_kv.ort`) | MIT |
| Reranker upgrade (tested, not used) | `cross-encoder/ettin-reranker-32m-v1` @ `b33e5ceb5110773ea9cf5e00c9bedc83a8c2afdd` | same layout as the 17m | — | Apache-2.0 |
| Reranker, desktop option (tested 2026-09-30) | `cross-encoder/ettin-reranker-68m-v1` @ `d166fa88ddde3c42bc3ee92f7df476d941c8204a`, exported by `rerank.py` to `data/models/ettin-68m` (`--ettin-dir`) | `onnx/model.onnx` (fp32), `onnx/model_nb8.onnx`, head `2_Dense`, `3_LayerNorm`, `4_Dense` | — | Apache-2.0 |

Notes:

- Qwen3.6-35B-A3B does not fit in the 12 GB of phone RAM. For harder questions, Ling's thinking mode reasons before it answers.
- The reranker is the stock Ettin-17m, fine-tuned by listwise distillation (KL over each pool) from `cross-encoder/ettin-reranker-400m-v1` (revision `5dca36282a5d85f368d2544002513a29159b4c9e` in the local Hugging Face cache). The data is 23,012 phone pools of up to 24 passages: 11,475 NQ-open train questions, 2,635 Stack Exchange titles and 8,902 questions that a local Gemma model wrote from pack passages. Training took 3 epochs, 17 minutes on the RTX 5060 Ti. The model card on Hugging Face has the full settings. Results are in [BENCHMARKS](BENCHMARKS.md#desktop-reranker-distillation-and-selective-int8-2026-10-11-to-2026-10-13).
- `model_sel8.onnx` comes from `rerank.py sel8`. Plain dynamic int8 loses about 6 points of NQ hit@1, because the input of layer 0's MLP output projection has large activation outliers. `sel8` keeps that one MatMul in fp32 and matches it by name suffix, because an exported graph prefixes it with `/m/`.
- The small model file is a pure Q4_0 of MiniCPM5-2B: `llama-quantize --pure --imatrix <bartowski's imatrix> --output-tensor-type q4_0 --token-embedding-type q4_0` on the official F16 (`openbmb/MiniCPM5-2B-GGUF` @ `2079a22f`). llama.cpp repacks only Q4_0 weights for ARM, and the mixed Q4_0 files on Hugging Face keep the output head in Q6_K. The pure file prefills 12–15% faster and decodes 8–9% faster on the Pixel, at the same quality within noise ([BENCHMARKS](BENCHMARKS.md#pixel-10-llama-bench-minicpm5-2b-2026-10-19)).
- The Rust reranker sorts the pairs by token length before it makes batches of 8, so a short pair does not get padded to the longest one.
- Voice input comes as the optional model pack `voice-en` (role `stt`, 257 MB, `voice-en.tar` in the release; build: `packbuild model --dir data/models/stt/moonshine-medium-streaming-en --file encoder.ort --role stt`, see PACKS.md). The mic button shows when the pack is installed. `scripts/dev-push.sh stt` still copies the model to `filesDir/stt/` for developer builds, and the app uses that copy when the pack is absent. The mic button streams Moonshine's live text into the question field. The user edits it, then sends. The app loads the model from the pack folder with `loadFromFiles`, so the library's downloader (OkHttp, WorkManager) never runs, and the build drops those dependencies. The model is loaded only while the mic is on. On the desktop it holds about 750 MB after load and 830–900 MB while streaming, so the app frees it before the LLM answers. Moonshine Small Streaming (123M, 136 MB on disk) peaked at 565 MB on the desktop and heard the test clip about as well. It is the fallback if memory is tight.
- Moonshine's AAR bundles its own ONNX Runtime 1.23.2 (minimal build) under the same file name as ours, and its libraries need the symbol version `VERS_1.23.2`, which our 1.30.0 does not define (the load failed with `cannot locate symbol "OrtGetApiBase"`). `android/app/build.gradle.kts` repacks the AAR with `patchelf` (Nix dev shell): Moonshine's copy becomes `libonnxruntime_moonshine.so`, and both runtimes load side by side. Tested on the Pixel 10 (loads and streams).
- Thinking mode (the brain button): Ling gets "detailed thinking on" and an open `<think>` block instead. `commonplace-llm` streams the reasoning between `<think>` and `</think>`. At `Settings::think_budget` tokens (default 256), it closes the reasoning itself with a closing sentence plus `</think>`; a bare `</think>` was often ignored, and the model went on reasoning inside the answer (2026-10-02). If the model still writes its own `</think>` later, the engine moves everything before it back into the reasoning and the answer gets its full token budget from there. An answer that reaches the answer-token cap ends at its last complete sentence or list line (`GenStats::truncated`).
- The reader returns the best span of at most 30 tokens in each of the top 3 passages. It shows a span only for simple lookups whose margin over "no answer" is at least 3. The margin cannot catch confident wrong spans (seen: 17.4), so the app names the source of every snippet, and the written answer still follows.
- Models considered and not used: K2-Horizon-7B needs the MBZUAI llama.cpp fork (`k2-horizon` architecture). TinyLettuce has no ONNX export yet. EttinX-nli-xs has one (`onnx-community/EttinX-nli-xs-ONNX`), but the app does not use it.

- The leaf-mt ONNX graph includes mean pooling and the 384→1024 projection. Its `sentence_embedding` output goes straight to the dense index. The app keeps the first 512 dimensions and normalizes them.
- The Ettin ONNX graph stops at the transformer. The Rust core applies the sentence-transformers head itself: CLS token → Dense 256 (GELU) → LayerNorm → Dense 1. The Rust scores match a NumPy reference to three decimals (`commonplace rerank`).
- Thinking is off by default. Ling-3.0-tiny needs "detailed thinking off" at the end of the system turn and `\n<think></think>` after the assistant header. llama.cpp `b11240` maps its template to the Ling 2.0 one, which adds neither, so `commonplace-llm` adds both.
- LLM files ship as model packs (`packbuild model`), which the app imports like knowledge packs.
- `commonplace-llm` saves the state of the answer system prompt to a file with `llama_state_seq_save_file`. The app uses `<data dir>/llm-state/`, and the CLI takes `--llm-state <dir>`. The file name is a hash of the model file name, its size and the system prompt. A new process loads the file only if its stored tokens equal the prompt's tokens; otherwise it prefills the prompt and writes a new file (`.tmp`, then a rename). At most 4 files stay, and the oldest goes first. For the 1,096-token answer prompt, the file is 26 MB.
- Three softmax heads on the leaf-mt question embedding take the place of word lists. `pipeline/commonplace_pipeline/heads.py` trains them, and the core compiles them in:
  - The turn-kind head: question, expand ("tell me more"), reformat or chat.
  - The follow-up head: the message needs the last turn ("why is it green?"), or it stands alone.
  - The intent head: lookup, explain, compare or calc.

  A costly label has its own probability bar, set by cross-validation: expand 0.635, reformat 0.639, chat 0.576 and calc 0.926.
- Property vectors (`pipeline/commonplace_pipeline/prop_vectors.py`, compiled in as `wikidata_props.json`) put each kept Wikidata property, and population density, in the leaf-mt space. The core picks the facts that a question asks about by the cosine to these vectors.

## Offline build only

| Role | Repo @ revision | File | SHA-256 |
|---|---|---|---|
| Document embedder (`embed.py` makes the dense codes of every knowledge pack on a GPU) | `mixedbread-ai/mxbai-embed-large-v1` @ `b33106f585b9ce46904ad7443a3b52b7a63e231c` | `onnx/model.onnx` | `adb53ed475faa339bfad3bd2bdb7e6a30b4f47280ade9811f81bef7953f9ab77` |

The phone never runs this model. It embeds only the passages at build time. The phone embeds questions with leaf-mt, which works with these document codes. `pipeline/README.md` has the commands.

## Runtimes

| Component | Version | Notes |
|---|---|---|
| llama.cpp | tag `b11240` (git submodule `third_party/llama.cpp`) | Static build. Android arm64 uses `GGML_CPU_ARM_ARCH=armv8.2-a+dotprod+i8mm` and KleidiAI. x86_64 uses explicit AVX2/FMA/F16C flags. |
| ONNX Runtime | Android AAR `com.microsoft.onnxruntime:onnxruntime-android:1.30.0`; desktop `onnxruntime` 1.27.1 (nixpkgs) | Loaded at run time (`ort` crate, `load-dynamic`, C API level 22). |
| Moonshine Voice | Android AAR `ai.moonshine:moonshine-voice:0.1.5`; desktop test `moonshine-voice==0.1.5` (PyPI) | JNI + C++ runtime for the voice model. Repacked without its ONNX Runtime copy. |
| LiteRT-LM | `com.google.ai.edge.litertlm:litertlm-android:0.17.1` | Engine option. It needs a `.litertlm` model pack. It runs Gemma 4 E2B on the CPU. The Tensor G5 NPU path is blocked. |
