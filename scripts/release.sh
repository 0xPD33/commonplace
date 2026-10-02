#!/usr/bin/env bash
# App release and packs release. Both write to dist/<version-or-tag>/ (override with OUT=dir).
#   KEYSTORE=path.jks KEY_ALIAS=commonplace scripts/release.sh app <version>
#     Signed arm64 APK. Fails if the APK has the INTERNET permission.
#     (apksigner asks for the keystore password; set KS_PASS=... to pass it non-interactively)
#   scripts/release.sh packs <tag> [pack_id...]       e.g. packs-2026-09 enwiki-core ling3-tiny wikidata-facts
#     Verify and split the packs (default: the starter set), then write SHA256SUMS and catalog.json.
#     Packs already in OUT stay in the catalog. LIBRARY=dir reads the pack directories (default data/library/packs).
#     REPO=owner/name sets the download URLs (default 0xPD33/commonplace, a default that may change).
#     CATALOG_ASSET=file receives a copy of catalog.json for the APK build (default android/app/src/main/assets/catalog.json).
#   scripts/release.sh upload <tag>
#     Create the GitHub pre-release <tag> and upload everything in OUT. This is the only subcommand that writes to GitHub.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=${1:?usage: $0 app <version> | packs <tag> [pack_id...] | upload <tag>}
NAME=${2:?usage: $0 $MODE <version-or-tag> ...}
shift 2
OUT=${OUT:-dist/$NAME}
[[ $MODE == upload ]] || mkdir -p "$OUT"

case $MODE in
app)
  : "${KEYSTORE:?set KEYSTORE to the release keystore (keep it out of the repository)}"
  : "${KEY_ALIAS:=commonplace}"
  scripts/build-android.sh release arm64-v8a
  UNSIGNED=android/app/build/outputs/apk/release/app-release-unsigned.apk
  BT=$ANDROID_HOME/build-tools/37.0.0
  "$BT/zipalign" -f -p 16 "$UNSIGNED" "$OUT/aligned.apk"
  PASS_ARGS=()
  [[ -n ${KS_PASS:-} ]] && PASS_ARGS=(--ks-pass "env:KS_PASS")
  "$BT/apksigner" sign --ks "$KEYSTORE" --ks-key-alias "$KEY_ALIAS" "${PASS_ARGS[@]}" --out "$OUT/commonplace-$NAME.apk" "$OUT/aligned.apk"
  rm "$OUT/aligned.apk"
  "$BT/apksigner" verify --print-certs "$OUT/commonplace-$NAME.apk" | head -3
  if "$BT/aapt2" dump permissions "$OUT/commonplace-$NAME.apk" | grep -q "android.permission.INTERNET"; then
    echo "INTERNET permission in the release APK" >&2
    exit 1
  fi
  ls -la "$OUT"
  ;;
packs)
  PACKS=("$@")
  [[ ${#PACKS[@]} -gt 0 ]] || PACKS=(enwiki-core ling3-tiny wikidata-facts)
  : "${LIBRARY:=data/library/packs}"
  : "${REPO:=0xPD33/commonplace}"
  : "${CATALOG_ASSET:=android/app/src/main/assets/catalog.json}"
  PB=${PB:-core/target/release/packbuild}
  for p in "${PACKS[@]}"; do
    "$PB" verify --pack "$LIBRARY/$p"
    # GitHub rejects release assets of 2 GiB (2147483648 bytes) or more.
    "$PB" split --pack "$LIBRARY/$p" --out "$OUT" --part-size 2000000000
  done
  [[ -z $(find "$OUT" -size +2147483647c) ]] || { echo "release asset over the 2 GiB limit" >&2; exit 1; }
  rm -f "$OUT/catalog.json" "$OUT/SHA256SUMS"
  scripts/catalog.py --dist "$OUT" --library "$LIBRARY" --tag "$NAME" --repo "$REPO" --out "$OUT/catalog.json"
  cp "$OUT/catalog.json" "$CATALOG_ASSET"
  (cd "$OUT" && sha256sum -- * > SHA256SUMS)
  ls -la "$OUT"
  ;;
upload)
  gh release view "$NAME" >/dev/null 2>&1 || gh release create "$NAME" --prerelease --title "$NAME" --notes "Commonplace packs $NAME. Check downloads against SHA256SUMS."
  gh release upload "$NAME" "$OUT"/* --clobber
  ;;
*)
  echo "unknown mode: $MODE" >&2
  exit 2
  ;;
esac
