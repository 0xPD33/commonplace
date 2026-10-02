"""English Wiktionary (kaikki.org JSONL extract; CC BY-SA 4.0 and GFDL) → packbuild input.

One article per word, one passage per part of speech: pronunciation, numbered senses, etymology.
Inflected forms ("cats"), alternative spellings, misspellings and proper names are skipped.
Every article is marked not linkable, so the entity linker never takes "bank" to the dictionary
instead of the Wikipedia article; keyword and dense search still find the entries.
Popularity is the number of translations, a stand-in for how common the word is.

  python -m commonplace_pipeline.wiktionary --input data/raw/kaikki/kaikki.org-dictionary-English.jsonl --out data/work/wiktionary-en
"""

from __future__ import annotations

import argparse
import json

import pyarrow as pa
import pyarrow.parquet as pq

from .chunk import pack_blocks
from .packwrite import PackWriter

SKIP_POS = {"name", "character", "symbol", "punct", "punctuation mark"}
SKIP_TAGS = {"form-of", "alt-of", "misspelling", "abbreviation-of"}
MAX_SENSES = 8


def entry_block(e: dict) -> str | None:
    senses = [s for s in e.get("senses", []) if not (SKIP_TAGS & set(s.get("tags", []))) and not s.get("form_of") and not s.get("alt_of")]
    glosses = [s["glosses"][-1] for s in senses if s.get("glosses")][:MAX_SENSES]
    if not glosses:
        return None
    ipa = next((s["ipa"] for s in e.get("sounds", []) if s.get("ipa")), "")
    lines = [f"{e['word']} ({e['pos']}){' ' + ipa if ipa else ''}"] + [f"{i}. {g}" for i, g in enumerate(glosses, 1)]
    if e.get("etymology_text"):
        lines.append(f"Etymology: {e['etymology_text'][:600]}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    w = PackWriter(a.out)
    word, blocks, pop = None, [], 0

    def flush():
        if word and blocks:
            chunks = [("", t) for t in pack_blocks(blocks)]
            w.add(word, chunks, popularity=pop, url_title=word.replace(" ", "_"), oneliner=blocks[0].split("\n")[1][3:300])

    with open(a.input) as f:
        for line in f:
            e = json.loads(line)
            if e.get("lang_code") != "en" or e.get("pos") in SKIP_POS:
                continue
            if e["word"] != word:
                flush()
                word, blocks, pop = e["word"], [], 0
            b = entry_block(e)
            if b:
                blocks.append(b)
                pop += len(e.get("translations", []))
    flush()
    w.close()
    t = pq.read_table(f"{a.out}/articles.parquet")
    pq.write_table(t.append_column("linkable", pa.array([False] * t.num_rows)), f"{a.out}/articles.parquet", compression="zstd")


if __name__ == "__main__":
    main()
