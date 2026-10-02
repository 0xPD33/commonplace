# Install

## On a phone (reviewer path, about 10 minutes)

You need an Android phone with 64-bit ARM, Android 12 or later, and about 9 GB of free storage (Wikipedia starter pack 3.5 GB, fast model 4.6 GB). All of English Wikipedia (`enwiki`, 19.2 GB, from the dump of 2026-09-01) replaces the starter pack. Commonplace targets Google Pixels on GrapheneOS with 12 GB of RAM. It does not need Google Play Services.

1. On any computer or on the phone, download from the GitHub release page:
   - `commonplace-<version>.apk`
   - all files of the starter pack: `enwiki-core.tar.part001`, `…part002`, … and `enwiki-core.pack.json`
   - all files of the fast model pack: `ling3-tiny.tar.part001`, … and `ling3-tiny.pack.json`
2. Put the files in the phone's **Downloads** folder (the phone's browser saves them there).
3. Install the APK. Android asks you to allow installs from your browser or file manager.
4. Open Commonplace and tap **Open Library**, then **Import pack**.
5. Select all files of one pack (long-press the first file, then tap the others) and tap **Select**.
   - The app copies and checks every byte (SHA-256) while it imports.
   - When the import finishes, the app offers to delete the downloaded files. Tap **Delete** to free the space.
6. Import the model pack the same way.
7. Go back and ask a question. The first question loads the model into memory (about 10–20 seconds).

To confirm that the app is offline, turn on airplane mode, or open **Settings → Apps → Commonplace → Permissions**: there is no network permission.

Optional packs:
- `enwiki` replaces `enwiki-core` with all of English Wikipedia (import it, then remove `enwiki-core` or keep it; the app skips a pack that a larger pack replaces).
- `wikidata-facts` adds numbers and dates (population, area, dates of birth) as fact chips.
- The deep model pack (Qwen3.6-35B-A3B, 16 GB) enables **Think harder**.

The app refuses an import that would take the total above 50 GB.

## Build from source

### With Nix (NixOS or any Linux with Nix)

```sh
git clone --recurse-submodules <repo> commonplace && cd commonplace
nix develop                         # Rust + Android targets, cargo-ndk, SDK 37, NDK 29, JDK 17, emulator, uv
scripts/fetch-models.sh encoders llm-small
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
| Python 3.12 and `uv` | for `pipeline/` and `eval/` only |

Then set `ANDROID_HOME` and `ANDROID_NDK_HOME=$ANDROID_HOME/ndk/29.0.14206865` and run the same `scripts/` commands.
The CI workflow (`.github/workflows/ci.yml`) uses exactly this path.

### Desktop CLI

```sh
cd core && cargo build --release -p commonplace-cli -p packbuild && cd ..
export ORT_DYLIB_PATH=/path/to/libonnxruntime.so   # nix develop sets this
core/target/release/commonplace --model data/models/llm/Ling-3.0-tiny-Q4_0.gguf ask "How do tides work?"
core/target/release/commonplace retrieve "tides" --k 10
core/target/release/commonplace info
```

### Development on a device or emulator

```sh
scripts/emulator.sh start                        # headless AOSP x86_64 emulator (no Play Services)
scripts/build-android.sh debug x86_64
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
scripts/dev-push.sh simplewiki lfm25-1.2b        # copy packs from data/library/packs over adb (debug builds)
python3 scripts/e2e_emulator.py                  # drives the app and writes artifacts/e2e/<stamp>/index.html
python3 scripts/e2e_emulator.py --import-dir data/dist/simplewiki   # also tests the file-picker import
```
