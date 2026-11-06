# Models

Every model that Commonplace uses, with its pinned revision and file hash. `scripts/fetch-models.sh` downloads these exact revisions into `data/models/` and writes `data/models/SHA256SUMS`. The licenses of the models differ from the license of the code. The model packs ship their license text and credit as `NOTICE.txt` (`pipeline/notices/`).

## On the phone

| Role | Hugging Face repo @ revision | File | SHA-256 | License |
|---|---|---|---|---|
| Fast model (CPU, pack `ling3-tiny`, read into RAM) | `bartowski/Ling-3.0-tiny-GGUF` @ `ea072726af0d2e8ba325b2f90fc0efa762105a91` | `Ling-3.0-tiny-Q4_0.gguf` (4.62 GB) | `c1a548fd60cfb5a46d6e5fd7635224dec473664c9320a656132106bf7ce23cfb` | MIT |
| Small model (CPU, pack `minicpm5-2b`, the default below 10 GB of RAM) | `0xPD33/MiniCPM5-2B-pureQ4_0-GGUF` @ `76674264cc1a1b11939b0e6875571d4aae84cb18` | `MiniCPM5-2B-pureQ4_0.gguf` (1.42 GB) | `d998b9433fe6e9b20e0dde7780e6c435a8a9cd893538ea70cbf1e09ce28cd5ae` | Apache-2.0 |
| Default on a Pixel 10 (pack `gemma4-e2b-npu`, LiteRT-LM on the Tensor G5 NPU) | `litert-community/gemma-4-E2B-it-litert-lm` @ `b3ca0d2f076785a8f4b2219ddbd2bdb99954eae1` | `gemma-4-E2B-it_Google_Tensor_G5.litertlm` (3.11 GB) | `af1082986639ecde7db95d91be6fe54f8b6b458104734c5bafc204e69d6852dc` | Apache-2.0 |
| Query embedder | `MongoDB/mdbr-leaf-mt` @ `1ed41b22ce166d66c24f88ebfc340e1f03adb20f` | `onnx/model_quantized.onnx` + `.onnx_data` (APK); `onnx/model.onnx` (desktop) | `2a3541f3…d8855` + `65dc11da…fce907`; `a3baf432…6a5` + `d23ff906…ded` | Apache-2.0 |
| Reranker (Ettin-17m, distilled, see below) | `0xPD33/ettin-reranker-17m-commonplace` @ `ebc0d6c8afdce3af49e12c6fd0b7cc6a6c398769` | `onnx/model_sel8.onnx` (phone), `onnx/model.onnx` (desktop), `onnx/model_nb8.onnx`, `onnx/model_quint8_avx2.onnx` (emulator), head `2_Dense`, `3_LayerNorm`, `4_Dense` | `5ab82f79…41349de`, `aca0130b…f29886`, `f2779800…77ce52`, `0ca6e421…964f22` | Apache-2.0 |
| Extractive reader (featured snippet) | `tomasmcm/nlpconnect-deberta-v3-xsmall-squad2-onnx` @ `96a6338a770f28b36ace1915a3c22685534ffe1b` | `onnx/model_int8.onnx` (90.5 MB) + `tokenizer.json` | `5959dfed4c2ef73027db026732669a2ca983e83b983b472f7423448ff6e1eb7e` | Apache-2.0 |
| Voice input (pack `voice-en`) | Moonshine Medium Streaming EN, 245M parameters, `download.moonshine.ai/model/medium-streaming-en/quantized_26_08_21` | 8 `.ort` files + `streaming_config.json` + `tokenizer.bin` (257 MB) | `12915e76…0c37` (`encoder.ort`), `193bb366…9486` (`decoder_kv.ort`) | MIT |
| Read aloud (pack `tts-en`) | `gradium/phonon-31m-en` @ `92741c5b07d085ff840e26fea8ce43110b87852f` | `model.q8.gguf` (64.5 MB) + `config.json`, `tokenizer.json`, `default-voice.safetensors` | `a7d9aad2…272dbae` (`model.q8.gguf`), `dc537478…d0f1a` (`default-voice.safetensors`) | Apache-2.0 |

## Compiled into the core

These are small trained files in `core/`. `pipeline/README.md` has the training commands.

- **Three softmax heads on the leaf-mt question embedding** take the place of word lists:
  - Turn-kind head: question, expand ("tell me more"), check ("are you sure?"), reformat or chat. A costly label has its own probability bar, set by cross-validation: expand 0.51, check 0.734, reformat 0.806 and chat 0.634.
  - Follow-up head: the message needs the last turn ("why is it green?"), or it stands alone.
  - Intent head: lookup, explain, compare or calc.
- **Learned fusion** (`fusion_model.json`): a linear layer and 16 tanh units over the ranks and scores of each candidate, pack statistics and popularity. It never sees the text. It keeps the passages that the reranker would rank highest if it saw every candidate. `--fusion rrf` restores reciprocal-rank fusion.
- **Sentence head** (`sentence_head.json`): one linear layer on the reranker's own token states. It scores each sentence piece of a passage, and the core grows one window per passage from the best piece.
- **Only Ling uses learned fusion, the sentence head and the follow-up check on reformat requests** (`ModelProfile::learned_search`). MiniCPM and Gemma failed the release gate with them, so they use reciprocal-rank fusion and word-overlap windows ([BENCHMARKS](BENCHMARKS.md#experiments-and-decisions), 2026-11-06).
- **Property vectors** (`wikidata_props.json`): each kept Wikidata property in the leaf-mt space. The core picks the facts that a question asks about by the cosine.
- **Ling's output rows** (`ling_head_rows.bin`): 97,168 of Ling's 157,184 output rows that the app computes, plus the rows of the tokens in the current prompt. The list keeps answers exact on the tested sets (102 of 102 on the desktop). With the last-layer patch, it saves about 88 MB of RSS. A new list can change answers, so run the release gate after you replace the file.

## Notes on the models

- **Reranker.** It is the stock Ettin-17m, fine-tuned by listwise distillation from `cross-encoder/ettin-reranker-400m-v1` on 23,012 phone pools of up to 24 passages. The model card has the settings, and [BENCHMARKS](BENCHMARKS.md#retrieval) has the results.
- **Reranker file `model_sel8.onnx`.** Plain dynamic int8 loses about 6 points of NQ hit@1, because the input of layer 0's MLP output projection has large activation outliers. `rerank.py sel8` keeps that one MatMul in fp32.
- **Reranker head.** The Ettin ONNX graph stops at the transformer. The Rust core applies the head itself: CLS token → Dense 256 (GELU) → LayerNorm → Dense 1. It sorts the pairs by token length before it makes batches of 8.
- **Query embedder.** The leaf-mt ONNX graph includes mean pooling and the 384→1024 projection. The app keeps the first 512 dimensions and normalizes them.
- **Small model file.** It is a pure Q4_0: `llama-quantize --pure --imatrix <bartowski's imatrix> --output-tensor-type q4_0 --token-embedding-type q4_0` on the official F16 (`openbmb/MiniCPM5-2B-GGUF` @ `2079a22f`). llama.cpp repacks only Q4_0 weights for ARM. The pure file prefills 12–15% faster and decodes 8–9% faster on the Pixel, at the same quality within noise ([BENCHMARKS](BENCHMARKS.md#llama-bench)).
- **Ling's prompt.** Ling has a shorter system prompt than the other models (1,031 cached tokens; before 0.4.0, 1,300), with two worked answers. Its how-to prompt asks for numbered steps. MiniCPM and Gemma keep the longer prompts.
- **Prompt state.** `commonplace-llm` saves the state of the answer system prompt to a file. The app uses `<data dir>/llm-state/`, and the CLI takes `--llm-state <dir>`. A new process loads the file only if its stored tokens equal the prompt's tokens. At most 4 files stay, and a 1,096-token prompt gave a file of 26 MB.
- **Thinking mode.** Thinking is off by default. Ling needs "detailed thinking off" at the end of the system turn and `\n<think></think>` after the assistant header, and `commonplace-llm` adds both. In thinking mode, the model gets an open `<think>` block. At `Settings::think_budget` tokens (default 256), the code closes the reasoning with a closing sentence plus `</think>`, because a bare `</think>` was often ignored.
- **Reader.** It returns the best span of at most 30 tokens in each of the top 3 passages, and only for simple lookups with a margin of at least 3 over "no answer". The margin cannot catch confident wrong spans, so the app names the source of every snippet.

### Voice input

The mic button streams Moonshine's live text into the question field, and the user edits it before sending. The model loads only while the mic is on, and on the desktop it holds 750–900 MB, so the app frees it before the LLM answers. The app loads it with `loadFromFiles`, so the library's downloader never runs and the build drops those dependencies. `scripts/dev-push.sh stt` copies the model to `filesDir/stt/` for developer builds.

Moonshine's AAR bundles its own ONNX Runtime 1.23.2 under the same file name as ours, and its libraries need a symbol version that our 1.30.0 does not define. `android/app/build.gradle.kts` repacks the AAR with `patchelf`, so both runtimes load side by side.

### Read aloud

- **System engine (default).** Android's `TextToSpeech`, only with a voice where `isNetworkConnectionRequired == false`, for the answer's language (`TextClassifier.detectLanguage`, else the phone's language). Without such a voice, the answer shows a message and nothing plays.
- **Bundled voice (pack `tts-en`).** Phonon 31m English, through Gradium's ptts runtime. It reads English answers only, and other languages go to the system engine. It always uses the pack's default voice, and the app has no voice-cloning input. It loads for each answer in under 0.2 s and frees about 0.2 GB after the answer. `RAYON_NUM_THREADS=2` is set only while it runs, because the core's search reads the same variable.
- **Text.** The app speaks the answer without Markdown marks, citation numbers or the source list. It stops when a new question starts or the Ask screen goes away, and it does not read while a model writes. For the bundled voice, ICU spells out years, ordinals, numbers and units first. The system engine gets the digits.
- **Why Phonon.** On the Pixel 10, Pocket TTS int8 ran at about real time and stuttered. Supertonic 2's license forbids use "to provide medical advice", which conflicts with the medical packs. Kokoro dropped numbers and names. Piper needs espeak-ng, which sherpa-onnx 1.x links in under GPL-3.0 ([BENCHMARKS](BENCHMARKS.md#read-aloud-2026-11-02)).

## Offline build only

| Role | Repo @ revision | File | SHA-256 |
|---|---|---|---|
| Document embedder (`embed.py` makes the dense codes of every knowledge pack on a GPU) | `mixedbread-ai/mxbai-embed-large-v1` @ `b33106f585b9ce46904ad7443a3b52b7a63e231c` | `onnx/model.onnx` | `adb53ed475faa339bfad3bd2bdb7e6a30b4f47280ade9811f81bef7953f9ab77` |

The phone never runs this model. It embeds questions with leaf-mt, which works with these document codes.

## Runtimes

| Component | Version | Notes |
|---|---|---|
| llama.cpp | tag `b12032` (git submodule `third_party/llama.cpp`), plus `patches/llama.cpp/` | The build applies the patches to a copy in `OUT_DIR`, so the submodule stays upstream. Android arm64 uses `GGML_CPU_ARM_ARCH=armv8.2-a+dotprod+i8mm+fp16` and KleidiAI. x86_64 uses explicit AVX2/FMA/F16C flags. |
| ONNX Runtime | Android AAR `com.microsoft.onnxruntime:onnxruntime-android:1.30.0`; desktop `onnxruntime` 1.27.1 (nixpkgs) | Loaded at run time (`ort` crate, `load-dynamic`, C API level 22). |
| Moonshine Voice | Android AAR `ai.moonshine:moonshine-voice:0.1.5`; desktop test `moonshine-voice==0.1.5` (PyPI) | JNI + C++ runtime for the voice model. |
| LiteRT-LM | `com.google.ai.edge.litertlm:litertlm-android:0.18.1` | Runs a `.litertlm` model pack. The file name picks the accelerator: `_Google_Tensor_` the NPU, `-gpu` the GPU, any other file the CPU. Speculative decoding is on for the NPU (Settings). If the pack cannot start, the app uses llama.cpp. |
| Phonon runtime (`ptts-ffi`, arm64 only) | Built from `gradium-ai/xn-ptts` @ `31e4e9f80275e432b570e17f5ccbefa895eff109` (version 0.6.0). `android/app/src/main/vendorLibs/arm64-v8a/libptts_ffi.so` (7.3 MB), SHA-256 `aa6f06e9faed875451d519d365358a18f08268132bc9b8b6bda02915609aaddc`. | Build, from the checkout: `RUSTFLAGS="-C strip=symbols -C link-arg=-Wl,-z,max-page-size=16384 -C target-cpu=generic -C target-feature=+dotprod,+fp16" cargo ndk -t arm64-v8a build --release -p ptts-ffi`. The app calls it through JNA (`PhononTTS.kt`). There is no x86_64 build, so on the emulator the bundled voice says that it is not available. |
| LiteRT Google Tensor dispatch library | Built from `google-ai-edge/LiteRT` @ `ca21dc99de51c76bdcc572afeea1dd937d3c9e5c`, the commit that LiteRT-LM 0.18.1 pins in its `WORKSPACE`. `android/app/src/main/vendorLibs/arm64-v8a/libLiteRtDispatch_GoogleTensor.so`, SHA-256 `a2bfb09b3ac9f0a6ee43a1a90a0733872ae113c0439c9a5d1448c40920044a6b`. | Google's prebuilt v2.2.0 library crashes with LiteRT-LM 0.18.1. Build: the LiteRT image `docker_build/hermetic_build.Dockerfile` (Bazel 7.4.1, NDK r28b); remove the Google-internal `//litert/vendors/google_tensor/hooks` dependency from `litert/vendors/google_tensor/dispatch/BUILD`; `bazel build -c opt --cxxopt=--std=c++17 --config=android_arm64 //litert/vendors/google_tensor/dispatch:dispatch_api_so`. The APK keeps native libraries uncompressed on disk (`useLegacyPackaging`) because LiteRT-LM loads this one by path. |

## Tested, not used

`scripts/fetch-models.sh` pins the ones that it still gets (`llm-lfm`, `llm-maple`, `llm-gemma`).

| Model | Why not |
|---|---|
| `LiquidAI/LFM2.5-8B-A1B-GGUF`, `LFM2.5-1.2B-Instruct-GGUF` | The models before Ling and MiniCPM. LFM Open License v1.0: commercial use only below US$10M annual revenue. |
| LFM3-1.7B, Spark-X2.6-1.7B | Lower scores than MiniCPM on the dev set (0.524–0.576 and 0.478 against 0.621). |
| `litert-community/gemma-4-E2B-it-litert-lm`, `gemma-4-E2B-it.litertlm` | GPU or CPU through LiteRT-LM. The NPU file is the one that ships. |
| `deepgrove/maple-preview-GGUF` (1B active of 20B) | 0.40 against Ling's 0.44 on 63 seeds. It decodes 1.65× faster and prefills 21% slower. |
| `bartowski/Qwen_Qwen3.6-35B-A3B-GGUF` | It does not fit in 12 GB of RAM. |
| `cross-encoder/ettin-reranker-17m-v1` | The stock reranker, before the distilled one (2026-10-13). |
| `cross-encoder/ettin-reranker-32m-v1`, `-68m-v1` | Larger rerankers. `-68m` scored 0.486 at a card time of 3.9 s, and it is a desktop option (`--ettin-dir`). |
| Pocket TTS by Kyutai through sherpa-onnx | The bundled voice of developer builds until 2026-11-02. See [Read aloud](#read-aloud). |
| K2-Horizon-7B, TinyLettuce, EttinX-nli-xs | Considered only: a llama.cpp fork, no ONNX export, or no use in the app. |
