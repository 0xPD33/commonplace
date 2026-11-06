# Agent guide: Commonplace

Commonplace is an offline research app for Android: a Rust core (`core/`) with retrieval, packs and llama.cpp,
a Kotlin app (`android/`), the pack pipeline (`pipeline/`), and the eval harness (`eval/`). `README.md` and `docs/`
describe the design. This file holds the rules and traps that are not obvious from the code.

## Hard rules

- Never add the `INTERNET` permission, runtime network calls or Play Services. Packs arrive by browser download and import.
- Ship only content and models that allow redistribution without use bans: no NC or ND content, no OpenRAIL-style
  use restrictions (the app ships medical packs), and no GPL code in the APK. Record every new component in
  `scripts/licenses.py` and the pack's notice in `pipeline/notices/`.
- Keep Stack Exchange text out of any training or fine-tuning data. Its dumps since 2024 forbid LLM training; retrieval packs are fine.
- Do not decide what a user message means with hand-written word lists, filler sets or regexes. Use a model or a
  trained head. Hard-coded rules are fine for deterministic facts: arithmetic, units, citation checks, number spell-out.
- Upload to Hugging Face (`0xPD33/commonplace-packs`) or publish a release only when the maintainer asks.

## Evidence

- Record every measurement in `docs/BENCHMARKS.md` with the device, the build hash and the date.
- A change to retrieval, prompts, models or packs needs an eval run and the release gate (`eval/README.md`,
  `report.py --gate <reference run>`). The test split is locked: run it to report, never to tune.
- Keep accuracy and performance tests apart. Accuracy: the eval sets in `eval/` with the judge, only when answer
  text can change. Speed and heat: `scripts/bench/perf.jsonl` (9 demanding prompts, no judge) on a cool phone
  (≤34 °C at the start); never the eval sets.
- Social images, charts and illustrations follow `docs/STYLE.md`. Claude Code has the project skill `commonplace-visuals`
  (`.claude/skills/`, local to this checkout) with a render helper.

## Build

- Work inside `nix develop`. Desktop: `cd core && cargo build --release`. Android: `scripts/build-android.sh debug arm64-v8a`.
- The Nix Android SDK is read-only and has only the platforms and build-tools that `flake.nix` lists.
- `android/app/src/main/vendorLibs/` holds prebuilt native libraries (the Tensor dispatch library and Phonon's
  `libptts_ffi.so`). Their sources and build commands are in `docs/MODELS.md`. Building outside code needs the maintainer's approval.
- The build stamp comes from `git describe --dirty`, so any uncommitted file marks a build as dirty. Commit first, then build a release.
- `third_party/llama.cpp` is a submodule: run `git submodule update --init third_party/llama.cpp` in a new worktree.
  Git refuses to remove a worktree with a submodule, so a clean, merged worktree needs `git worktree remove --force`.

## Shared machine and parallel agents

- Several agents work in this repo at once (Herdr panes). Check with the others before you merge to master,
  rebuild shared binaries, or run long GPU or CPU jobs.
- Eval runs use `core/target/release/commonplace` in the main checkout. Do not rebuild it while an eval runs;
  build in a worktree instead.

## Phone testing (Pixel 10, GrapheneOS)

- Debug and release builds share the package `app.commonplace` with different keys, and the release build blocks
  `run-as`. Install test builds next to the installed app with `ORG_GRADLE_PROJECT_appIdSuffix=.spike`, then push packs with
  `PKG=app.commonplace.spike scripts/dev-push.sh <pack_id>...`.
- Keep the screen awake while plugged in (`adb shell svc power stayon usb`). Close the notification shade before a
  screenshot or a recording: it shows private messages. Never send a blind BACK key.
- For speed comparisons, force the post-install compile (`adb shell cmd package compile -m speed-profile -f <pkg>`),
  wait about 90 s, and interleave the builds (A, B, A, B). Runs right after an install were 30% slower.
- The speed and eval drivers keep the app on screen, so they cannot see the 3 GiB limit of a hidden app on Android 17. Before a
  release, turn stay-awake off, hide the app and lock the phone on each model, then read `adb shell dumpsys activity exit-info <pkg>`.
- Restore every phone setting you change, such as the default TTS engine (`tts_default_synth`).

## Native-code traps

- A JNI library that looks up a typed `invoke` on a Kotlin callback (sherpa-onnx did) needs `@JvmSerializableLambda`:
  Kotlin 2 compiles plain lambdas without that method, and the app aborts.
- `AudioTrack.stop()` resets `playbackHeadPosition`. Measure the remaining play-out before you call it.
- `RAYON_NUM_THREADS` is process-wide: Phonon and the Rust core read the same variable.

## Plan files

`ROADMAP.md` (goals) and `TODO.md` (open tasks) are local and not committed; `TODO-bounty.md` is the archived bounty
plan. Project memory is in `.memory/` (private, never committed).
