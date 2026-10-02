#!/usr/bin/env bash
# Download the permutans/wikidata-* tables that the wikidata step needs (Wikidata dump of 2026-05-07, ~20 GB).
# The author was still uploading source chunks on 2026-09-29 (finished that evening). This takes the latest revision and skips
# files already on disk: rerun it to pick up new chunks, then rerun the wikidata step.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=data/raw/wikidata
API=https://huggingface.co/api/datasets
HF=https://huggingface.co/datasets
# dataset:folder
SETS=(
  wikidata-claims:all
  wikidata-links:enwiki
  wikidata-labels:en
  wikidata-labels:mul
  wikidata-aliases:en
  wikidata-aliases:mul
  wikidata-claims_labels:en
)
mkdir -p "$OUT"
: > "$OUT/REVISIONS.tmp"
for s in "${SETS[@]}"; do
  ds=${s%%:*} dir=${s#*:}
  rev=$(curl -fsS --retry 8 --retry-all-errors --retry-delay 10 "$API/permutans/$ds" | jq -r .sha)
  echo "$ds/$dir $rev" >> "$OUT/REVISIONS.tmp"
  mkdir -p "$OUT/$ds/$dir"
  curl -fsS --retry 8 --retry-all-errors --retry-delay 10 "$API/permutans/$ds/tree/$rev/$dir" | jq -r '.[] | select(.type=="file") | .path' |
    while read -r p; do echo "$HF/permutans/$ds/resolve/$rev/$p $OUT/$ds/$p"; done
done | xargs -P 8 -n 2 sh -c '[ -s "$1" ] || { curl -fsSL --retry 5 -C - -o "$1.part" "$0" && mv "$1.part" "$1"; }'
echo "dump 2026-05-07" >> "$OUT/REVISIONS.tmp"
mv "$OUT/REVISIONS.tmp" "$OUT/REVISIONS"
