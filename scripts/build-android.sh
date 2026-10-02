#!/usr/bin/env bash
# Build the Rust core for Android, generate Kotlin bindings, stage encoder assets, and assemble the APK.
# Usage: scripts/build-android.sh [debug|release] [abi...]   (default: debug arm64-v8a x86_64)
# Run inside `nix develop` (or with ANDROID_HOME, ANDROID_NDK_HOME, cargo-ndk and JDK 17 on PATH).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
VARIANT=${1:-debug}
shift || true
ABIS=("$@")
[[ ${#ABIS[@]} -gt 0 ]] || ABIS=(arm64-v8a x86_64)
APP=android/app/src/main
M=data/models

[[ -d $M/leaf-mt && -d $M/ettin-17m ]] || scripts/fetch-models.sh encoders
[[ -f $M/reader/onnx/model_int8.onnx ]] || scripts/fetch-models.sh fastlane

# 1. Rust core → jniLibs/<abi>/libcommonplace_ffi.so (stripped).
targs=()
for a in "${ABIS[@]}"; do targs+=(-t "$a"); done
(cd core && cargo ndk "${targs[@]}" -P 31 -o "$ROOT/$APP/jniLibs" build --release -p commonplace-ffi)
STRIP=$ANDROID_NDK_HOME/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip
for a in "${ABIS[@]}"; do "$STRIP" --strip-debug "$APP/jniLibs/$a/libcommonplace_ffi.so"; done
# 2. Kotlin bindings from the host build of the same crate.
(cd core && cargo build --release -p commonplace-ffi)
rm -rf "$APP/java/uniffi"
(cd core && cargo run -q --release --bin uniffi-bindgen -- generate --no-format \
  --library target/release/libcommonplace_ffi.so --language kotlin --out-dir "$ROOT/$APP/java")

# 3. Encoders (quantized for the phone) as uncompressed assets.
E=$APP/assets/encoders
rm -rf "$E" && mkdir -p "$E/leaf-mt/onnx" "$E/ettin-17m/onnx"
cp $M/leaf-mt/tokenizer.json "$E/leaf-mt/"
cp $M/leaf-mt/onnx/model_quantized.onnx $M/leaf-mt/onnx/model_quantized.onnx_data "$E/leaf-mt/onnx/"
cp $M/ettin-17m/tokenizer.json "$E/ettin-17m/"
if [[ " ${ABIS[*]} " == *" arm64-v8a "* ]]; then cp $M/ettin-17m/onnx/model_nb8.onnx "$E/ettin-17m/onnx/"; fi
if [[ " ${ABIS[*]} " == *" x86_64 "* ]]; then cp $M/ettin-17m/onnx/model_quint8_avx2.onnx "$E/ettin-17m/onnx/"; fi
for d in 2_Dense 3_LayerNorm 4_Dense; do mkdir -p "$E/ettin-17m/$d" && cp $M/ettin-17m/$d/model.safetensors "$E/ettin-17m/$d/"; done
# Extractive reader for the featured snippet (the int8 file runs on arm64 and x86_64).
mkdir -p "$E/reader/onnx" && cp $M/reader/tokenizer.json "$E/reader/" && cp $M/reader/onnx/model_int8.onnx "$E/reader/onnx/"
(cd "$E" && find . -type f | sort > manifest.txt)

# 4. APK.
TASK=assemble${VARIANT^}
(cd android && ./gradlew --no-daemon -q "$TASK" -Pabis="$(IFS=,; echo "${ABIS[*]}")")
ls -la android/app/build/outputs/apk/"$VARIANT"/*.apk
