# Install

## On a phone

### What you need

- An Android phone with a 64-bit ARM CPU and Android 12 or later. Google Play Services are not necessary. I test on a Pixel 10 with GrapheneOS.
- 12 GB of RAM for the main model, which uses about 5 GB while it runs. On a phone with 8 GB, use the small model (see [More packs](#more-packs)).
- About 23 GB of free storage for the first import: the downloaded file and the installed packs. After the import, the app uses 11.3 GB.
- A Wi-Fi connection. The Starter set is one file of 11.3 GB.

The app has no `INTERNET` permission. You download the packs with the phone's browser from the Hugging Face dataset [`0xPD33/commonplace-packs`](https://huggingface.co/datasets/0xPD33/commonplace-packs). Then the app imports them from the **Downloads** folder.

### Steps

1. On the phone, open the [latest release](https://github.com/0xPD33/commonplace/releases/latest) and download `commonplace-<version>.apk`. Open the file and install the app. Android asks you to allow installs from your browser.
2. Open Commonplace. The **Get started** card names the Starter set and its size. Tap **Open Library**.
3. Under **Get more**, tap **Starter set**, then tap **Download**. The browser saves `commonplace-starter.tar` in **Downloads**. The Starter set holds three packs:
   - `enwiki-core`: English Wikipedia (dump of 2026-09-01). It holds the opening passages of the 2 million most-viewed articles and the full text of the 200,000 most-viewed articles.
   - `ling3-tiny`: Ling-3.0-tiny, the model that writes the answers.
   - `wikidata-facts`: numbers and dates from Wikidata, such as population, area and dates of birth.
4. Wait until the download is complete. Then go back to Commonplace and tap **Install from Downloads** in the Library.
5. Tap `commonplace-starter.tar`. To install several files in one step, long-press one file, tap the others, then tap **Select**.
   - The app checks every byte with SHA-256 while it copies the file. It ignores files that are not packs.
   - The progress card names each pack while the app installs it.
   - If the file is incomplete or damaged, the app names the file and installs nothing from the damaged part.
6. When the import ends, the app offers to delete the downloaded file. Tap **Delete** to free the space.
7. Go back and ask a question. The first question is slower, because the app loads the model into memory.

To check that the app is offline, turn on airplane mode. You can also open **Settings > Apps > Commonplace > Permissions**: the app has no network permission.

### More packs

You get each of these files in the same way: tap it under **Get more**, download it, then tap **Install from Downloads**.

| File | Size | Contents |
|---|---|---|
| Starter set (`commonplace-starter.tar`) | 11.3 GB | `enwiki-core`, `ling3-tiny` and `wikidata-facts` |
| Reference shelf (`commonplace-breadth.tar`) | 2.0 GB | 13 packs: open textbooks, Wikibooks, Wikiquote, Wikivoyage, Wiktionary, Wikiversity, DevDocs, ArchWiki, WikEM, OpenStax, MedlinePlus, CDC travel health and the CIA World Factbook |
| `enwiki.tar` | 19.2 GB | All 6.8 million English Wikipedia articles. It replaces `enwiki-core`, and the app then skips `enwiki-core`. |
| `arxiv-abs.tar` | 3.3 GB | 3.2 million arXiv abstracts |
| `stackexchange.tar` | 2.5 GB | 1.2 million question threads from 51 Stack Exchange sites |
| `lfm25-1.2b.tar` | 0.7 GB | LFM2.5-1.2B, a small model for phones with 8 GB of RAM. Select it in Settings. |

All files together use 35.5 GB on the phone. The app refuses an import that takes the total above 50 GB. [DATASETS.md](DATASETS.md) lists each pack with its source and license, and [MODELS.md](MODELS.md) lists the models.

In the Library, each knowledge pack has a switch. A pack that is off stays installed but the app does not search it.

### Install from a computer

1. Download the files from the folder `packs-2026-09` of the dataset [`0xPD33/commonplace-packs`](https://huggingface.co/datasets/0xPD33/commonplace-packs/tree/main/packs-2026-09).
2. Compare each file with `SHA256SUMS` in the same folder: `sha256sum -c SHA256SUMS --ignore-missing`.
3. Connect the phone with USB and copy the files to its **Downloads** folder.
4. On the phone, tap **Install from Downloads** in the Library and select the files.

A `.tar` pack that is not in the catalog installs the same way. A pack in parts installs too: select its `<pack_id>.pack.json` file and all its `.tar.partNNN` files in one step.

### My documents

To search your own files, tap **Add document** under **My documents** in the Library. The app accepts PDF, text and Markdown files and indexes them on the phone. Citations show the page. Tap **Ask this document** on a document to search only that document.

PDF import needs Android 15 or later. On Android 12 to 14, it works only with recent system updates. A scanned PDF without a text layer does not work.

### Thinking mode

The brain button next to the question box lets the model reason before it answers. Reasoning takes longer. Settings has a slider for the reasoning budget (256 tokens by default).

## Build from source

### With Nix (NixOS or any Linux with Nix)

```sh
git clone --recurse-submodules https://github.com/0xPD33/commonplace.git commonplace && cd commonplace
nix develop                         # Rust + Android targets, cargo-ndk, SDK 37, NDK 29, JDK 17, emulator, uv
scripts/fetch-models.sh encoders llm-fast
scripts/build-android.sh debug      # APK: android/app/build/outputs/apk/debug/app-debug.apk
```

### Without Nix

Install these tools:

| Tool | Version |
|---|---|
| Rust (rustup) with targets `aarch64-linux-android`, `x86_64-linux-android` | stable 1.90 or later |
| `cargo-ndk` | 4.x |
| Android SDK with `platforms;android-37.0`, `build-tools;37.0.0`, `ndk;29.0.14206865` | — |
| JDK | 17 |
| CMake, Ninja, Clang (for bindgen) | CMake 3.22 or later |
| `patchelf` | The app build renames the ONNX Runtime library inside the Moonshine voice AAR. |
| Python 3.12 and `uv` | `scripts/fetch-models.sh encoders` runs `uv` once to make the phone's reranker file. `pipeline/` and `eval/` also need them. |

Set `ANDROID_HOME` and `ANDROID_NDK_HOME=$ANDROID_HOME/ndk/29.0.14206865`. Then run the same `scripts/` commands.
The CI workflow (`.github/workflows/ci.yml`) uses this path.

### Desktop CLI

```sh
cd core && cargo build --release -p commonplace-cli -p packbuild && cd ..
export ORT_DYLIB_PATH=/path/to/libonnxruntime.so   # nix develop sets this
core/target/release/commonplace --model data/models/llm/Ling-3.0-tiny-Q4_0.gguf ask "How do tides work?"
core/target/release/commonplace retrieve "tides" --k 10
core/target/release/commonplace info
```

The CLI reads packs from `data/library/packs/`. To install a downloaded pack or bundle, run `tar -xf <file>.tar -C data/library/packs`.
Add `--think` to `ask` for thinking mode and `--rewrite` to turn on the question rewrite step.

### Development on a device or emulator

```sh
scripts/emulator.sh start                        # headless AOSP x86_64 emulator (no Play Services)
scripts/build-android.sh debug x86_64
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
LIBRARY=data/library-dev scripts/dev-push.sh simplewiki lfm25-1.2b   # copy packs over adb (debug builds)
python3 scripts/e2e_emulator.py                  # drives the app and writes artifacts/e2e/<stamp>/index.html
python3 scripts/e2e_emulator.py --import-dir data/dist/simplewiki   # also tests the file-picker import
```

`simplewiki` is a small development pack. [PACKS.md](PACKS.md) shows how to build it.
