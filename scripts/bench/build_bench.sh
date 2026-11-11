#!/usr/bin/env bash
# Android llama-bench: stock (today's app flags, no patches) and ship (0.4.0 patches 0001-0010, 0012, 0013, +fp16).
set -eo pipefail
S=${OUT:-/tmp/commonplace-bench}; P=$(cd "$(dirname "$0")/../../patches/llama.cpp" && pwd)
for v in stock:armv8.2-a+dotprod+i8mm ship:armv8.2-a+dotprod+i8mm+fp16; do n=${v%%:*}; arch=${v#*:}; d=$S/bench-$n; rm -rf $d; mkdir -p $d/src
  (cd "$(dirname "$P")/../third_party/llama.cpp" && git archive HEAD) | tar x -C $d/src
  [ $n = ship ] && for p in $P/*.patch; do (cd $d/src && GIT_CEILING_DIRECTORIES=$d git apply $p); done
  cmake -S $d/src -B $d/build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_TOOLCHAIN_FILE=$ANDROID_NDK_HOME/build/cmake/android.toolchain.cmake -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-31 -DANDROID_STL=c++_static -DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=$arch -DGGML_CPU_KLEIDIAI=ON -DBUILD_SHARED_LIBS=OFF -DGGML_OPENMP=OFF -DGGML_BACKEND_DL=OFF -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_SERVER=OFF >/dev/null 2>&1
  nice -n 10 cmake --build $d/build --target llama-bench -j 8 2>&1 | tail -1
done
echo BENCH BUILDS DONE
