# Commonplace

An offline research assistant for Android. It answers questions from a library of Wikipedia and other reference text stored on the phone, cites every claim, and never touches the network: the app has no `INTERNET` permission.

Built for the poidh bounty "Build the Best Offline AI Research App for Android". Target hardware: Google Pixel on GrapheneOS, 12 GB RAM, no Google Play Services.

## How it works

The naive design spends the phone's compute reading raw Wikipedia at query time. Commonplace does the reading ahead of time on a desktop, and the phone mostly looks things up.

1. **Search in about one second.** Keyword search (tantivy BM25) and semantic search (512-bit binary codes in an IVF index, queried with the 23M-parameter leaf-mt encoder) run in parallel. Reciprocal-rank fusion merges them, and the Ettin-17m cross-encoder reranks the top 40.
2. **An instant answer card.** The best passage, with the matching sentence highlighted, Wikidata facts and the source list appear before the model writes a word.
3. **A small, fast model writes the answer.** Ling-3.0-tiny (7.9B total, 1.3B active parameters, MIT) runs fully in RAM through llama.cpp. It reads compact evidence, not whole articles. The system prompt's state is cached, so each question only pays for its own evidence.
4. **Deterministic checks instead of a second model.** Citations to sources that do not exist are removed. Sentences with numbers that are not in the evidence are underlined. Arithmetic and unit conversions run in code, not in the model.
5. **Hard questions get a plan.** Comparisons, "why" questions and follow-ups go through a short grammar-constrained planning call that splits them into sub-searches.

An optional deep mode swaps in Qwen3.6-35B-A3B, streamed from flash, for slow but stronger answers.

## Status (2026-09-29)

| Part | State |
|---|---|
| Rust core: packs, hybrid retrieval, rerank, orchestration, citations, tools, telemetry | Works on desktop and Android |
| Desktop CLI (`commonplace ask / retrieve / bench / serve-eval`) | Works |
| Android app (Compose): ask, streaming answers, sources, reader, library import, settings, diagnostics | Works in the emulator with English Wikipedia; the E2E script passes all steps (`artifacts/e2e/`) |
| Packs: `enwiki` (41.5M passages, 12.8 GB), `enwiki-core` starter (7.2M passages, 2.75 GB), `wikidata-facts`, keyword-only `wikivoyage-en`, `stackexchange`, `openstax`, `arxiv-abs`, `medlineplus`, `simplewiki` (dev), model packs | Built locally; parts ready in `data/dist/` |
| LiteRT-LM TPU backend | Code complete; needs the Pixel (gate G0) |
| Doc2query and fact cards | Code complete; GPU runs not started |
| Evaluation harness | Ready; waits for the seed questions |
| Pixel 10 measurements | Not started (no device yet) |

## Try it

- **Phone:** see [docs/INSTALL.md](docs/INSTALL.md).
- **Desktop CLI:**
  ```sh
  nix develop            # or install the tools listed in docs/INSTALL.md
  scripts/fetch-models.sh encoders llm-small
  cd core && cargo build --release -p commonplace-cli -p packbuild && cd ..
  core/target/release/commonplace --model data/models/llm/LFM2.5-1.2B-Instruct-Q4_0.gguf ask "Why is the sky blue?"
  ```
  The CLI reads packs from `data/library/packs/`. [docs/PACKS.md](docs/PACKS.md) shows how to build them.

## Repository

| Path | Contents |
|---|---|
| `core/` | Rust workspace: `commonplace-core`, `commonplace-llm` (llama.cpp FFI), `commonplace-ffi` (UniFFI), `commonplace-cli`, `packbuild` |
| `android/` | Kotlin + Jetpack Compose app |
| `pipeline/` | Python build steps: chunking, Plan A conversion, doc2query, fact cards, Wikidata |
| `eval/` | Evaluation harness (Claude with web search as the baseline and judge) |
| `scripts/` | Model and data downloads, Android build, emulator, E2E driver, dev push |
| `docs/` | [INSTALL](docs/INSTALL.md), [MODELS](docs/MODELS.md), [DATASETS](docs/DATASETS.md), [PACKS](docs/PACKS.md), [BENCHMARKS](docs/BENCHMARKS.md), [EVAL](docs/EVAL.md) |
| `third_party/llama.cpp` | Pinned submodule (`b11240`) |

## License

Code: Apache-2.0. Packs carry the license of their source (Wikipedia: CC BY-SA 4.0; Wikidata: CC0). Model licenses are in [docs/MODELS.md](docs/MODELS.md).
