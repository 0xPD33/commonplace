"""Write core/commonplace-llm/src/ling_head_rows.bin: the rows of Ling's output head that the app computes.

Ling's vocabulary has 157,184 tokens, and the head reads about 240 MB per token on the phone. Most rows never win in
the packs' languages. Patch `0015-head-rows` computes the listed rows, plus the rows of the tokens in the current
prompt. The list holds:

  - tokens seen at least twice in a sample of each pack, and every token of the enwiki-core sample
  - tokens of Ling's answers in eval/runs, except the answers to the dev, test, canary and abstention questions
  - all special tokens and all pieces of at most 2 bytes
  - padding to a multiple of 16, the block size of the head's matrix kernel

A new list can change answers: run the release gate after you replace the file. The answers in eval/runs change over
time, so a new run does not rebuild the 0.4.0 file byte for byte (docs/BENCHMARKS.md, 2026-11-05).

  nix develop -c env PYTHONPATH=$PWD/pipeline:$PWD/third_party/llama.cpp/gguf-py pipeline/.venv/bin/python \
    -m commonplace_pipeline.head_rows --out core/commonplace-llm/src/ling_head_rows.bin
"""

import argparse
import collections
import glob
import json
import random
import struct
from compression.zstd import ZstdDict, decompress
from pathlib import Path

from gguf import GGUFReader
from tokenizers import Regex, Tokenizer, models, pre_tokenizers

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data/models/llm/Ling-3.0-tiny-Q4_0.gguf"
PACKS = ROOT / "data/library/packs"
FRAMES_PER_PACK = 1500
BLOCK = 16
# llama.cpp's split for the `bailingmoe2` vocabulary (llama-vocab.cpp).
SPLIT = r"'(?:[sSdDmMtT]|[lL][lL]|[vV][eE]|[rR][eE])|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}| ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]|\s+(?!\S)|\s+"
HELD_OUT = ("dev", "test", "canary", "abstention")


def pack_sample(store):
    """The texts of a seeded sample of a pack's frames. The record format is in the core's pack/frames.rs."""
    idx = (store / "frames.idx").read_bytes()
    n_frames = struct.unpack_from("<Q", idx, 20)[0]
    off = struct.unpack_from(f"<{n_frames + 1}Q", idx, 28)
    zd = ZstdDict((store / "dict.zstd").read_bytes())
    texts = []
    random.seed(1)
    with open(store / "frames.bin", "rb") as f:
        for fr in sorted(random.sample(range(n_frames), min(FRAMES_PER_PACK, n_frames))):
            f.seek(off[fr])
            raw = decompress(f.read(off[fr + 1] - off[fr]), zstd_dict=zd)
            n = struct.unpack_from("<I", raw, 0)[0]
            o = struct.unpack_from(f"<{n + 1}I", raw, 4)
            base = 4 + 4 * (n + 1)
            texts += [raw[base + o[i] + 8 : base + o[i + 1]].decode("utf8", "ignore") for i in range(n)]
    return texts


def ling_answers():
    held = {json.loads(l)["id"] for s in HELD_OUT for l in open(ROOT / f"eval/{s}.jsonl")}
    answers = set()
    for path in glob.glob(str(ROOT / "eval/runs/*/answers.jsonl")):
        for line in open(path):
            r = json.loads(line)
            if r.get("answer") and r.get("id") not in held and "Ling" in ((r.get("record") or {}).get("model_id") or ""):
                answers.add(r["answer"])
    return sorted(answers)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    r = GGUFReader(MODEL)
    names = r.fields["tokenizer.ggml.tokens"].contents()
    types = r.fields["tokenizer.ggml.token_type"].contents()
    merges = [tuple(m.split(" ", 1)) for m in r.fields["tokenizer.ggml.merges"].contents()]
    tok = Tokenizer(models.BPE(vocab={s: i for i, s in enumerate(names)}, merges=merges))
    tok.pre_tokenizer = pre_tokenizers.Sequence(
        [pre_tokenizers.Split(Regex(SPLIT), "isolated"), pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)])
    ids = lambda texts: [t for e in tok.encode_batch(texts, add_special_tokens=False) for t in e.ids]

    counts = collections.Counter()
    keep = set()
    for store in sorted(PACKS.glob("*/store")):
        if (store / "frames.idx").exists():
            c = collections.Counter(ids(pack_sample(store)))
            counts.update(c)
            if store.parent.name == "enwiki-core":
                keep |= set(c)
            print(store.parent.name, len(c), flush=True)
    keep |= {t for t, n in counts.items() if n >= 2}
    keep |= set(ids(ling_answers()))
    keep |= {i for i, (s, t) in enumerate(zip(names, types)) if (t != 1 and "reserved_token" not in s) or (t == 1 and len(s) <= 2)}
    fill = iter(set(range(len(names))) - keep)
    while len(keep) % BLOCK:
        keep.add(next(fill))

    out = sorted(keep)
    Path(a.out).write_bytes(struct.pack(f"<{len(out)}i", *out))
    print(f"{len(out)} rows of {len(names)}: {a.out}")


if __name__ == "__main__":
    main()
