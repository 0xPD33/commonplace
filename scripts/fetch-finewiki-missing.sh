#!/usr/bin/env bash
# Keep the finewiki English articles (2025-08) that the Plan A `enwiki` pack lacks, for the keyword-only
# `enwiki-extra` pack. Plan A text misses about a quarter of the most-viewed articles (Russia, Paris, Moon).
# Streams one ~2.5 GB shard at a time and deletes it after filtering, so peak disk use stays small.
# Run inside `nix develop`. Output: data/raw/finewiki-en-missing/*.parquet
set -euo pipefail
cd "$(dirname "$0")/.."
REV=8bd13e72e6a002407649b3e898535f42ceb1aeb9
BASE=https://huggingface.co/datasets/HuggingFaceFW/finewiki/resolve/$REV/data/enwiki
OUT=data/raw/finewiki-en-missing
mkdir -p "$OUT"
[ -s "$OUT/have-titles.txt" ] || sqlite3 data/library/packs/enwiki/meta.sqlite "SELECT title FROM articles" > "$OUT/have-titles.txt"
shards=$(curl -fsSL "https://huggingface.co/api/datasets/HuggingFaceFW/finewiki/tree/$REV/data/enwiki" | jq -r '.[].path | select(endswith(".parquet")) | split("/")[-1]')
for s in $shards; do
  [ -s "$OUT/$s" ] && continue
  curl -fsSL --retry 5 -C - -o "$OUT/$s.part" "$BASE/$s"
  uv run --project pipeline python - "$OUT/$s.part" "$OUT/$s" "$OUT/have-titles.txt" <<'EOF'
import sys, pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc
src, dst, have = sys.argv[1:]
have = pa.array(open(have, encoding="utf-8").read().splitlines())
t = pq.read_table(src)
keep = t.filter(pc.invert(pc.is_in(t["title"], value_set=have)))
pq.write_table(keep, dst + ".tmp", compression="zstd")
print(f"{dst}: kept {keep.num_rows} of {t.num_rows}")
EOF
  mv "$OUT/$s.tmp" "$OUT/$s"
  rm -f "$OUT/$s.part"
done
