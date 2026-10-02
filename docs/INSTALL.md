# Install

## On a phone

You need an Android phone with a 64-bit ARM CPU and Android 12 or later. Commonplace targets Google Pixels on GrapheneOS with 12 GB of RAM. It does not need Google Play Services.

Storage: the starter pack (3.5 GB) and the main model (4.6 GB) need about 8.2 GB. Wikidata facts need 3.2 GB more. All of English Wikipedia (`enwiki`, 19.2 GB, dump of 2026-09-01) replaces the starter pack.

The app has no `INTERNET` permission. You download the files with a browser and import them with the Android file picker.

### Steps

1. On any computer or on the phone, open the GitHub release page of the app. Download `commonplace-<version>.apk`.
2. Open the release `packs-2026-09`. Download all files of each pack that you want:
   - the starter pack: `enwiki-core.tar.part001`, `enwiki-core.tar.part002`, ... and `enwiki-core.pack.json`
   - the main model: `ling3-tiny.tar.part001`, ... and `ling3-tiny.pack.json`
   - optional: more packs from the list below
3. Put the files in the **Downloads** folder of the phone. The phone's browser saves them there.
4. Install the APK. Android asks you to allow installs from your browser or file manager.
5. Open Commonplace. Tap **Open Library**, then **Import pack**.
6. Select all files of one pack. Long-press the first file, tap the others, then tap **Select**.
   - The app copies the files and checks every byte (SHA-256) during the import.
   - When the import ends, the app offers to delete the downloaded files. Tap **Delete** to free the space.
7. Import the model pack the same way.
8. Go back and ask a question. The first question is slower, because the app loads the model into memory. On the Pixel 10 the first word appears after about 10 s.

Each pack has parts of at most 2 GB. Import all parts of a pack in one step.

To confirm that the app is offline, turn on airplane mode. You can also open **Settings > Apps > Commonplace > Permissions**: the app has no network permission.

### Models

| Pack | Size | Use |
|---|---|---|
| `ling3-tiny` | 4.6 GB | Main model (Ling-3.0-tiny). Needs about 5 GB of RAM while it runs. |
| `lfm25-1.2b` | 0.7 GB | Small model (LFM2.5-1.2B) for phones with 8 GB of RAM. |

### Optional packs

- `enwiki` (19.2 GB) replaces `enwiki-core` with all of English Wikipedia. The app skips a pack that a larger pack replaces.
- `wikidata-facts` (3.2 GB) adds numbers and dates (population, area, dates of birth) as fact chips.
- The breadth packs add travel guides, Stack Exchange answers, textbooks, medical pages, technical documentation, arXiv abstracts, a dictionary and more. [DATASETS.md](DATASETS.md) lists each pack with its size and license.

The app refuses an import that would take the total above 50 GB.

### Thinking mode

The brain button next to the question box lets the model reason before it answers. Reasoning takes longer. Settings has a slider for the reasoning budget (256 tokens by default).

## Build from source

### With Nix (NixOS or any Linux with Nix)

```sh
git clone --recurse-submodules <repo> commonplace && cd commonplace
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

The CLI reads packs from `data/library/packs/`. To install a downloaded pack, run `cat <pack>.tar.part* | tar -x -C data/library/packs`.
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
