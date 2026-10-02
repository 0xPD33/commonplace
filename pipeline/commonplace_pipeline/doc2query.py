"""doc2query: generate 3-5 search questions per passage with an LLM.

Coverage order: the lead passage of the top --lead-articles articles, then every passage of the top
--full-articles articles. Output: <out-dir>/questions.parquet (passage_id uint32, questions str, "\\n"-joined).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .llmgen import ShardStore, add_endpoint_args, load_passages, load_popular, run_jobs, strip_numbering

K, MIN_Q, MAX_WORDS = 5, 3, 20
PROMPT = """Write {k} different questions that a curious person might type into a search box and that this
passage directly answers. Vary phrasing and specificity. Include the article subject by name.
One per line, no numbering.

Article: {title}{section}
Passage: {text}"""
SCHEMA = pa.schema([("key", pa.uint32()), ("questions", pa.string())])


def parse_questions(out: str) -> list[str]:
    qs: list[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        q = strip_numbering(line)
        n = len(q.split())
        if n < 3 or n > MAX_WORDS or q.endswith(":") or q.lower() in seen:
            continue
        seen.add(q.lower())
        qs.append(q)
    return qs[:K]


def select(work: Path, lead_articles: int, full_articles: int) -> list[int]:
    top = load_popular(work, max(lead_articles, full_articles)).to_pylist()
    ids = [a["first_passage"] for a in top[:lead_articles]]
    seen = set(ids)
    for a in top[:full_articles]:
        ids += [p for p in range(a["first_passage"], a["first_passage"] + a["n_passages"]) if p not in seen]
    return ids


def merge(store: ShardStore, out: Path) -> None:
    t = store.read_all()
    t = t.filter(pc.greater(pc.utf8_length(t["questions"]), 0))
    t = t.sort_by("key").rename_columns(["passage_id", "questions"])
    pq.write_table(t, out / "questions.parquet", compression="zstd")
    counts = [len(q.split("\n")) for q in t.column("questions").to_pylist()]
    few = sum(c < MIN_Q for c in counts)
    print(f"questions.parquet: {t.num_rows} passages, {sum(counts)} questions, {few} with fewer than {MIN_Q}")


def main() -> None:
    ap = argparse.ArgumentParser()
    add_endpoint_args(ap)
    ap.add_argument("--lead-articles", type=int, default=1_000_000)
    ap.add_argument("--full-articles", type=int, default=200_000)
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--temperature", type=float, default=0.6)
    args = ap.parse_args()
    work = Path(args.work)
    out = Path(args.out_dir or args.work)
    store = ShardStore(out, "q", SCHEMA)
    if not args.merge_only:
        ids = select(work, args.lead_articles, args.full_articles)
        done = store.done_keys()
        pending = [p for p in ids if p not in done][: args.limit or None]
        passages = load_passages(work, pending)
        titles = dict(zip(*pq.read_table(work / "articles.parquet", columns=["article_id", "title"]).to_pydict().values()))

        async def worker(chat, pid, p):
            section = f" — {p['section_path']}" if p["section_path"] else ""
            prompt = PROMPT.format(k=K, title=titles[p["article_id"]], section=section, text=p["text"])
            qs = parse_questions(await chat(prompt, args.max_tokens, args.temperature))
            return [{"key": pid, "questions": "\n".join(qs)}]

        asyncio.run(run_jobs(args, store, ((p, passages[p]) for p in pending), worker))
    merge(store, out)


if __name__ == "__main__":
    main()
