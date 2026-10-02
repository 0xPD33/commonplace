#!/usr/bin/env bash
# Download Plan A inputs (prebuilt mxbai binary codes + row-aligned 2023-11 passage text). Dev-time only.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=data/raw/plan-a
HF=https://huggingface.co/datasets
QRD_REV=$(curl -s $HF/../api/datasets/sentence-transformers/quantized-retrieval-data | jq -r .sha)
TXT_REV=a659477aa9be6b5a07d8f2ae3106420d265e70c3
mkdir -p "$OUT/text"
get() { [[ -s "$2" ]] && return; curl -fL --retry 5 -C - -o "$2.part" "$1" && mv "$2.part" "$2"; }
get "$HF/sentence-transformers/quantized-retrieval-data/resolve/$QRD_REV/wikipedia_ubinary_faiss_50m.index" "$OUT/wikipedia_ubinary_faiss_50m.index"
for i in $(seq -w 0 40); do
  f=train-000$i-of-00041.parquet
  get "$HF/mixedbread-ai/wikipedia-data-en-2023-11/resolve/$TXT_REV/data/$f" "$OUT/text/$f"
done
echo "qrd_rev=$QRD_REV text_rev=$TXT_REV" > "$OUT/REVISIONS"
# Pageviews (ranking prior, entity-link tie-break).
PV_REV=40a94fda39044e78a0fc3d92c07d4f2bef4c4587
get "$HF/NeuML/wikipedia-20260401/resolve/$PV_REV/pageviews.sqlite" data/raw/pageviews.sqlite
