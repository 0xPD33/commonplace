#!/usr/bin/env bash
# Retrieve against one pack alone and save the result to data/work/<pack_id>/check.txt.
# Usage (inside nix develop): scripts/check-pack.sh <pack_id> "<question>"
set -euo pipefail
cd "$(dirname "$0")/.."
id=$1 q=$2
lib=$(mktemp -d)
trap 'rm -rf "$lib"' EXIT
mkdir -p "$lib/packs"
ln -s "$PWD/data/library/packs/$id" "$lib/packs/$id"
{
  echo "\$ commonplace --library <only $id> --no-llm retrieve --k 5 \"$q\""
  core/target/release/commonplace --library "$lib" --no-llm retrieve --k 5 "$q"
} 2>&1 | tee "data/work/$id/check.txt"
