#!/usr/bin/env bash
# App release and packs release. Both write to dist/<version-or-tag>/ (override with OUT=dir).
#   KEYSTORE=path.jks KEY_ALIAS=commonplace scripts/release.sh app <version>
#     Signed arm64 APK. Fails if the APK has the INTERNET permission.
#     (apksigner asks for the keystore password; set KS_PASS=... to pass it non-interactively)
#   HF_REPO=<namespace>/<name> scripts/release.sh packs <tag> [pack_id...]   e.g. packs-2026-09 enwiki-core ling3-tiny wikidata-facts
#     Verify the packs (default: the starter set) and write each one as a single file <pack_id>.tar.
#     Also write the bundles (see BUNDLES below) whose packs are all in the list as commonplace-<bundle_id>.tar.
#     A pack inside such a bundle gets no single file.
#     Then write SHA256SUMS, catalog.json and README.md (the dataset card). Packs and bundles already in OUT stay in the catalog.
#     HF_REPO is the Hugging Face dataset repo (required, no default). The catalog URLs point into its folder <tag>:
#     https://huggingface.co/datasets/<HF_REPO>/resolve/main/<tag>/<file>?download=true
#     LIBRARY=dir reads the pack directories (default data/library/packs).
#     CATALOG_ASSET=file receives a copy of catalog.json for the APK build (default android/app/src/main/assets/catalog.json).
#   HF_REPO=<namespace>/<name> scripts/release.sh upload <tag>
#     Create the dataset repo if it is missing, upload the .tar files, catalog.json and SHA256SUMS of OUT to its folder <tag>,
#     and upload README.md to the repo root. Needs a write token (HF_TOKEN, or run `uvx --from huggingface_hub hf auth login`).
#     Run the same command again to resume an interrupted upload. This is the only subcommand that writes to Hugging Face.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE=${1:?usage: $0 app <version> | packs <tag> [pack_id...] | upload <tag>}
NAME=${2:?usage: $0 $MODE <version-or-tag> ...}
shift 2
OUT=${OUT:-dist/$NAME}
[[ $MODE == upload ]] || mkdir -p "$OUT"
# Bundles: one .tar file that holds several packs, so a user downloads one file. Edit this list to change them.
# One line per bundle: id|title|description|pack ids in tar order. BUNDLES=... in the environment replaces the list.
BUNDLES=${BUNDLES:-'starter|Starter set|Wikipedia (2M articles), the answer model and Wikidata facts.|enwiki-core ling3-tiny wikidata-facts
breadth|Reference shelf|Textbooks, Wikibooks, Wikiquote, Wikivoyage, Wiktionary, Wikiversity, programming documentation, ArchWiki, medical and travel health pages, and the World Factbook.|textbooks-en wikibooks-en wikiquote-en wikivoyage-en wiktionary-en wikiversity-en devdocs-en archwiki-en wikem-en openstax medlineplus cdc-travel factbook'}
need_repo() {
  [[ ${HF_REPO:-} =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || { echo "set HF_REPO to the Hugging Face dataset repo, <namespace>/<name>" >&2; exit 1; }
}

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
  need_repo
  PACKS=("$@")
  [[ ${#PACKS[@]} -gt 0 ]] || PACKS=(enwiki-core ling3-tiny wikidata-facts)
  : "${LIBRARY:=data/library/packs}"
  : "${CATALOG_ASSET:=android/app/src/main/assets/catalog.json}"
  PB=${PB:-core/target/release/packbuild}
  # A pack inside a bundle that gets built ships only in that bundle.
  BUNDLED=" "
  while IFS='|' read -r id _ _ members; do
    [[ -n $id ]] || continue
    for m in $members; do [[ " ${PACKS[*]} " == *" $m "* ]] || continue 2; done
    BUNDLED+="$members "
  done <<<"$BUNDLES"
  for p in "${PACKS[@]}"; do
    "$PB" verify --pack "$LIBRARY/$p"
    [[ $BUNDLED == *" $p "* ]] || "$PB" split --single --pack "$LIBRARY/$p" --out "$OUT"
  done
  BUNDLE_ARGS=()
  while IFS='|' read -r id title desc members; do
    [[ -n $id ]] || continue
    BUNDLE_ARGS+=(--bundle "$id|$title|$desc|$members")
    dirs=()
    for m in $members; do
      [[ " ${PACKS[*]} " == *" $m "* ]] || continue 2
      dirs+=("$LIBRARY/$m")
    done
    "$PB" bundle --pack "${dirs[@]}" --out "$OUT/commonplace-$id.tar"
  done <<<"$BUNDLES"
  rm -f "$OUT/catalog.json" "$OUT/SHA256SUMS" "$OUT/README.md"
  scripts/catalog.py --dist "$OUT" --library "$LIBRARY" --tag "$NAME" --repo "$HF_REPO" --out "$OUT/catalog.json" --readme "$OUT/README.md" "${BUNDLE_ARGS[@]}"
  cp "$OUT/catalog.json" "$CATALOG_ASSET"
  (cd "$OUT" && sha256sum -- *.tar catalog.json > SHA256SUMS)
  ls -la "$OUT"
  ;;
upload)
  need_repo
  HF=(uvx --from "huggingface_hub>=2.1" hf)
  "${HF[@]}" repos create "$HF_REPO" --type dataset --public --exist-ok
  "${HF[@]}" upload "$HF_REPO" "$OUT" "$NAME" --repo-type dataset --include "*.tar" --include catalog.json --include SHA256SUMS --commit-message "Add packs $NAME"
  "${HF[@]}" upload "$HF_REPO" "$OUT/README.md" README.md --repo-type dataset --commit-message "Update the dataset card for $NAME"
  ;;
*)
  echo "unknown mode: $MODE" >&2
  exit 2
  ;;
esac
