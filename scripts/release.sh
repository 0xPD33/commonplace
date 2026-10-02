#!/usr/bin/env bash
# Build a signed arm64 release APK and the distribution parts of the release packs.
# Usage: KEYSTORE=path.jks KEY_ALIAS=commonplace scripts/release.sh <version> [pack_id...]
#   (apksigner asks for the keystore password; set KS_PASS=... to pass it non-interactively)
# Output: dist/<version>/ with the APK, pack parts, pack.json files and SHA256SUMS.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=${1:?usage: $0 <version> [pack_id...]}
shift
PACKS=("$@")
[[ ${#PACKS[@]} -gt 0 ]] || PACKS=(enwiki-core ling3-tiny wikidata-facts)
: "${KEYSTORE:?set KEYSTORE to the release keystore (keep it out of the repository)}"
: "${KEY_ALIAS:=commonplace}"
OUT=dist/$VERSION
mkdir -p "$OUT"

scripts/build-android.sh release arm64-v8a
UNSIGNED=android/app/build/outputs/apk/release/app-release-unsigned.apk
BT=$ANDROID_HOME/build-tools/37.0.0
"$BT/zipalign" -f -p 16 "$UNSIGNED" "$OUT/aligned.apk"
PASS_ARGS=()
[[ -n ${KS_PASS:-} ]] && PASS_ARGS=(--ks-pass "env:KS_PASS")
"$BT/apksigner" sign --ks "$KEYSTORE" --ks-key-alias "$KEY_ALIAS" "${PASS_ARGS[@]}" --out "$OUT/commonplace-$VERSION.apk" "$OUT/aligned.apk"
rm "$OUT/aligned.apk"
"$BT/apksigner" verify --print-certs "$OUT/commonplace-$VERSION.apk" | head -3
if "$BT/aapt2" dump permissions "$OUT/commonplace-$VERSION.apk" | grep -q "android.permission.INTERNET"; then
  echo "INTERNET permission in the release APK" >&2
  exit 1
fi

for p in "${PACKS[@]}"; do
  core/target/release/packbuild verify --pack "data/library/packs/$p"
  core/target/release/packbuild split --pack "data/library/packs/$p" --out "$OUT"
done
(cd "$OUT" && sha256sum -- * > SHA256SUMS)
ls -la "$OUT"
