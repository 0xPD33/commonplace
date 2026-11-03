#!/usr/bin/env bash
# Phone speed of the 0.4.0 CPU changes: stock vs ship llama-bench, Ling and MiniCPM, mirrored order (A B B A per model),
# cool-down gate (skin <= 38 C, at most 4 min) before each run. Prefill: strict 6 threads on cpus 2-7; decode: the app's placement.
S=${OUT:-/tmp/commonplace-bench}; D=/data/local/tmp/cpb
for v in stock ship; do adb push -q $S/bench-$v/build/bin/llama-bench $D/b040-$v; done; adb shell "chmod +x $D/b040-*"
skin() { adb shell dumpsys thermalservice | grep "Temperature{.*mName=VIRTUAL-SKIN-LEGACY," | tail -1 | sed -E 's/.*mValue=([0-9.]+).*/\1/'; }
cool() { for i in $(seq 1 24); do t=$(skin); awk -v t="$t" 'BEGIN{exit !(t<=38.0)}' && return; sleep 10; done; }
num() { tail -2 | tr -d '"' | awk -F, '{printf "%.1f ", $(NF-1)}'; }
bench() { local v=$1 m=$2; cool; local t=$(skin)
  pp=$(adb shell "cd $D && ./b040-$v -m $m -C 0xFC --cpu-strict 1 -t 6 -p 512 -n 0 -d 0,1100 -r 2 -lm none -o csv 2>/dev/null" | num)
  tg=$(adb shell "cd $D && ./b040-$v -m $m -C 0xFC --cpu-strict 0 -t 4 -p 0 -n 64 -d 0,1100 -r 2 -lm none -o csv 2>/dev/null" | num)
  echo "$(date +%T) $(basename $m .gguf) $v skin=$t  pp512 d0,d1100: $pp  tg64 d0,d1100: $tg"
}
for m in minicpm.gguf ../bench/ling.gguf; do for v in stock ship ship stock; do bench $v $m; done; done
echo SHIP BENCH DONE
