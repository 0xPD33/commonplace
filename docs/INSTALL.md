# Install

## On a phone

The [README](../README.md#install-on-a-phone) has the requirements and the steps. This section adds the details.

- **Which starter set.** The app marks the set for your phone with **For this phone**. It counts a phone with less than 10 GB of RAM as an 8 GB phone. Ling uses about 5.4 GB while it runs, and MiniCPM5-2B about 2.6 GB. When the app goes off screen, it unloads Ling, then loads it again on return. MiniCPM5-2B and Gemma stay loaded for 5 minutes.
- **What a starter set holds.**
  - `enwiki-core`: English Wikipedia (dump of 2026-09-01). It has the opening passages of the 2 million most-viewed articles and the full text of the 200,000 most-viewed articles.
  - `ling3-tiny` or `minicpm5-2b`: the model that writes the answers.
  - `wikidata-facts`: numbers and dates from Wikidata, such as population, area and dates of birth.
- **Gemma on a Pixel 10.** When the pack is installed, Gemma becomes the answer model. Settings can still choose Ling. For a full answer, it is more than twice as fast as Ling (4.23 s against 10.0 s at the median).
- **A damaged file.** The app names the file and installs nothing from the damaged part. It ignores files that are not packs.

### More packs

Tap a file under **Get more**, download it, then tap **Install from Downloads**.

| File | Size | Contents |
|---|---|---|
| `commonplace-starter.tar` | 11.3 GB | `enwiki-core`, `ling3-tiny` and `wikidata-facts`, for 12 GB phones |
| `commonplace-starter-small.tar` | 8.1 GB | `enwiki-core`, `minicpm5-2b` and `wikidata-facts`, for 8 GB phones |
| `commonplace-breadth.tar` (Reference shelf) | 2.0 GB | 13 packs: open textbooks, Wikibooks, Wikiquote, Wikivoyage, Wiktionary, Wikiversity, DevDocs, ArchWiki, WikEM, OpenStax, MedlinePlus, CDC travel health and the CIA World Factbook |
| `enwiki.tar` | 19.2 GB | All 6.8 million English Wikipedia articles. It replaces `enwiki-core`. |
| `arxiv-abs.tar` | 3.3 GB | 3.2 million arXiv abstracts |
| `stackexchange.tar` | 2.5 GB | 1.2 million question threads from 51 Stack Exchange sites |
| `minicpm5-2b.tar` | 1.4 GB | The small model, for a phone with the 12 GB starter set. Select it in Settings. |
| `voice-en.tar` | 0.3 GB | Voice input in English. About 0.6 GB of extra RAM while the mic is on. The mic button appears on the Ask screen after the install. |
| `tts-en.tar` | 0.07 GB | Read aloud with a bundled English voice (Phonon) instead of the phone's own voice. About 0.2 GB of extra RAM while it reads. Choose it in Settings, under Read aloud. It needs an arm64 phone. |
| `gemma4-e2b-npu.tar` | 3.1 GB | Gemma 4 E2B for the Tensor G5 NPU. The app lists it only on phones with that chip. |

All files use 36.5 GB on the phone, and 39.7 GB with the Gemma pack. The app refuses an import that takes the total above 50 GB. In the Library, each knowledge pack has a switch: a pack that is off stays installed, but the app does not search it. [DATASETS](DATASETS.md) lists each pack with its source and license, and [MODELS](MODELS.md) lists the models.

### Updates

The app has no internet access, so it learns of new pack builds only from a new app version. When the app lists a newer build of a pack you have, the Library shows it under **Updates**. Download it and install it: it replaces the old pack.

### Install from a computer

1. Download the files from the dataset [`0xPD33/commonplace-packs`](https://huggingface.co/datasets/0xPD33/commonplace-packs/tree/main). The Reference shelf is in the folder `packs-2026-11`, and all other files are in `packs-2026-09`.
2. Compare each file with `SHA256SUMS` in the folder `packs-2026-11`: `sha256sum -c SHA256SUMS --ignore-missing`.
3. Connect the phone with USB and copy the files to its **Downloads** folder.
4. On the phone, tap **Install from Downloads** in the Library and select the files.

A `.tar` pack that is not in the catalog installs the same way. A pack in parts installs too: select its `<pack_id>.pack.json` file and all its `.tar.partNNN` files in one step.

### My documents

Tap **Add document** under **My documents** in the Library. The app accepts PDF, text and Markdown files and indexes them on the phone. Tap **Ask this document** to search only that document.

PDF import needs Android 15, or Android 12 to 14 with recent system updates. A scanned PDF without a text layer does not work.

## Build from source

### With Nix

```sh
git clone --recurse-submodules https://github.com/0xPD33/commonplace.git commonplace && cd commonplace
nix develop                         # Rust + Android targets, cargo-ndk, SDK 37, NDK 29, JDK 17, emulator, uv
scripts/fetch-models.sh encoders llm-fast
scripts/build-android.sh debug      # APK: android/app/build/outputs/apk/debug/app-debug.apk
```

### Without Nix

Install these tools, set `ANDROID_HOME` and `ANDROID_NDK_HOME=$ANDROID_HOME/ndk/29.0.14206865`, then run the same `scripts/` commands. The CI workflow (`.github/workflows/ci.yml`) uses this path.

| Tool | Version |
|---|---|
| Rust (rustup) with targets `aarch64-linux-android`, `x86_64-linux-android` | stable 1.90 or later |
| `cargo-ndk` | 4.x |
| Android SDK with `platforms;android-37.0`, `build-tools;37.0.0`, `ndk;29.0.14206865` | — |
| JDK | 17 |
| CMake, Ninja, Clang (for bindgen) | CMake 3.22 or later |
| `git` | The build applies `patches/llama.cpp/` to a copy of llama.cpp with `git`. |
| `patchelf` | The app build renames the ONNX Runtime library inside the Moonshine voice AAR. |
| Python 3.12 and `uv` | `scripts/fetch-models.sh encoders` runs `uv` once to make the phone's reranker file. `pipeline/` and `eval/` also need them. |

### Desktop CLI

```sh
cd core && cargo build --release -p commonplace-cli -p packbuild && cd ..
export ORT_DYLIB_PATH=/path/to/libonnxruntime.so   # nix develop sets this
core/target/release/commonplace --model data/models/llm/Ling-3.0-tiny-Q4_0.gguf ask "How do tides work?"
core/target/release/commonplace retrieve "tides" --k 10
core/target/release/commonplace info
```

The CLI reads packs from `data/library/packs/`. To install a pack or bundle, run `tar -xf <file>.tar -C data/library/packs`. Add `--think` to `ask` for thinking mode and `--rewrite` for the question rewrite step. Add `--llm-state data/llm-state` to keep the warm-up state between runs.

### Development on a device or emulator

```sh
scripts/emulator.sh start                        # headless AOSP x86_64 emulator (no Play Services)
scripts/build-android.sh debug x86_64
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
LIBRARY=data/library-dev scripts/dev-push.sh simplewiki minicpm5-2b   # copy packs over adb (debug builds)
python3 scripts/e2e_emulator.py                  # drives the app and writes artifacts/e2e/<stamp>/index.html
python3 scripts/e2e_emulator.py --import-dir data/dist/simplewiki   # also tests the file-picker import
```

`simplewiki` is a small development pack. [PACKS](PACKS.md) shows how to build it.
