#!/usr/bin/env bash
# Debug builds only: copy installed pack directories from data/library/packs into the app's
# internal library over adb (adb push to /data/local/tmp, then run-as cp).
# Usage: scripts/dev-push.sh <pack_id>...   (packs must exist under data/library/packs/)
#        scripts/dev-push.sh stt            (voice-input model from data/models/stt/, run scripts/fetch-models.sh stt first)
set -euo pipefail
cd "$(dirname "$0")/.."
PKG=app.commonplace
LIBRARY=${LIBRARY:-data/library}   # e.g. LIBRARY=data/library-dev for the simplewiki dev pack
TMP=/data/local/tmp/commonplace
[[ $# -gt 0 ]] || { echo "usage: $0 <pack_id>..." >&2; exit 2; }
adb shell mkdir -p "$TMP"
adb shell run-as $PKG mkdir -p files/library/packs
for id in "$@"; do
  if [[ $id == stt ]]; then
    src=data/models/stt/moonshine-medium-streaming-en dest=files/stt/moonshine-medium-streaming-en
    [[ -f $src/encoder.ort ]] || { echo "no model at $src (run scripts/fetch-models.sh stt)" >&2; exit 1; }
    echo "pushing stt ($(du -sh "$src" | cut -f1))"
    adb shell rm -rf "$TMP/stt" && adb push -q "$src/." "$TMP/stt" && adb shell chmod -R a+rX "$TMP/stt"
    adb shell run-as $PKG sh -c "'mkdir -p files/stt && rm -rf $dest && cp -r $TMP/stt $dest'"
    adb shell rm -rf "$TMP/stt"
    continue
  fi
  src=$LIBRARY/packs/$id
  [[ -f $src/manifest.json ]] || { echo "no pack at $src" >&2; exit 1; }
  echo "pushing $id ($(du -sh "$src" | cut -f1))"
  adb shell rm -rf "$TMP/$id"
  adb push -q "$src/." "$TMP/$id"
  adb shell chmod -R a+rX "$TMP/$id"
  adb shell run-as $PKG sh -c "'rm -rf files/library/packs/$id && cp -r $TMP/$id files/library/packs/$id'"
  adb shell rm -rf "$TMP/$id"
done
echo "done; restart the app (adb shell am force-stop $PKG) to reload the library"
