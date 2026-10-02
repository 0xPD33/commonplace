#!/usr/bin/env bash
# Start a headless AOSP emulator (no Play Services, like GrapheneOS) for app testing.
# Usage: scripts/emulator.sh [start|stop|wait]   Needs `nix develop` (emulator, avdmanager, adb) and /dev/kvm.
set -euo pipefail
cd "$(dirname "$0")/.."
export ANDROID_AVD_HOME=$PWD/data/avd
export ANDROID_EMULATOR_HOME=$PWD/data/avd
NAME=commonplace
IMAGE="system-images;android-36;default;x86_64"
LOG=data/logs/emulator.log
mkdir -p "$ANDROID_AVD_HOME" data/logs

wait_boot() {
  adb wait-for-device
  until [[ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]]; do sleep 2; done
  adb shell settings put global window_animation_scale 0
  adb shell settings put global transition_animation_scale 0
  adb shell settings put global animator_duration_scale 0
  echo "emulator ready: $(adb shell getprop ro.build.fingerprint | tr -d '\r')"
}

case "${1:-start}" in
  start)
    if adb devices | grep -q emulator-; then echo "emulator already running"; wait_boot; exit 0; fi
    if [[ ! -d $ANDROID_AVD_HOME/$NAME.avd ]]; then
      echo no | avdmanager create avd -n "$NAME" -k "$IMAGE" --force
      cat >> "$ANDROID_AVD_HOME/$NAME.avd/config.ini" <<EOF
hw.ramSize=8192
disk.dataPartition.size=24G
hw.cpu.ncore=6
hw.keyboard=yes
hw.gpu.enabled=yes
hw.gpu.mode=swiftshader_indirect
EOF
    fi
    nohup emulator -avd "$NAME" -no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect \
      -memory 8192 -cores 6 >"$LOG" 2>&1 &
    wait_boot
    ;;
  wait) wait_boot ;;
  stop) adb emu kill || true ;;
  *) echo "usage: $0 [start|stop|wait]" >&2; exit 2 ;;
esac
