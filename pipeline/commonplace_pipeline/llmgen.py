"""Shared runner for the LLM distillation steps (doc2query, cards).

It sends prompts to any OpenAI-compatible /chat/completions endpoint (vLLM, SGLang, llama-server)
with bounded concurrency, and writes results to numbered shard files in <out-dir>/shards/.
Every shard row carries a `key` column. On restart, the runner skips each key that a shard already holds.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

NUMBERING_RE = re.compile(r"^\s*(?:[-*•]+|\(?\d+[.)]|[QqFf]\d+[:.)])\s*")


def add_endpoint_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--work", required=True, help="chunker output dir (articles.parquet, passages.parquet)")
    ap.add_argument("--out-dir", help="shards and the merged parquet go here (default: --work)")
    ap.add_argument("--base-url", default="http://localhost:8000/v1", help="OpenAI-compatible base URL, ending in /v1")
    ap.add_argument("--model", default="default")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "none"))
    ap.add_argument("--concurrency", type=int, default=64)
    ap.add_argument("--checkpoint-secs", type=int, default=600, help="write a shard at least this often (<= 900)")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--extra", default='{"chat_template_kwargs": {"enable_thinking": false}}',
                    help="JSON merged into each request body")
    ap.add_argument("--limit", type=int, default=0, help="max jobs this run (0 = all)")
    ap.add_argument("--merge-only", action="store_true")


def strip_numbering(line: str) -> str:
    return NUMBERING_RE.sub("", line.strip()).strip().strip('"').strip()


def load_popular(work: Path, n: int) -> pa.Table:
    """The top-n articles by popularity, most popular first."""
    arts = pq.read_table(work / "articles.parquet", columns=["article_id", "title", "popularity", "first_passage", "n_passages"])
    idx = pc.sort_indices(arts, [("popularity", "descending"), ("article_id", "ascending")])
    return arts.take(idx[:n])


def load_passages(work: Path, ids: list[int]) -> dict[int, dict]:
    t = pq.read_table(work / "passages.parquet", columns=["passage_id", "article_id", "ordinal", "section_path", "text"],
                      filters=[("passage_id", "in", ids)] if ids else None)
    return {r["passage_id"]: r for r in t.to_pylist()}


class ShardStore:
    def __init__(self, out_dir: Path, prefix: str, schema: pa.Schema):
        self.dir = out_dir / "shards"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prefix, self.schema = prefix, schema
        self.files = sorted(self.dir.glob(f"{prefix}-*.parquet"))
        self.next = max((int(f.stem.rsplit("-", 1)[1]) for f in self.files), default=-1) + 1

    def done_keys(self) -> set:
        done: set = set()
        for f in self.files:
            done.update(pq.read_table(f, columns=["key"]).column("key").to_pylist())
        return done

    def write(self, rows: list[dict]) -> None:
        if not rows:
            return
        path = self.dir / f"{self.prefix}-{self.next:05d}.parquet"
        tmp = path.with_suffix(".tmp")
        pq.write_table(pa.Table.from_pylist(rows, self.schema), tmp, compression="zstd")
        tmp.rename(path)
        self.files.append(path)
        self.next += 1

    def read_all(self) -> pa.Table:
        return pa.concat_tables([pq.read_table(f) for f in self.files]) if self.files else self.schema.empty_table()


class Chat:
    def __init__(self, args: argparse.Namespace):
        self.url = args.base_url.rstrip("/") + "/chat/completions"
        self.model, self.extra = args.model, json.loads(args.extra)
        self.client = httpx.AsyncClient(timeout=args.timeout, headers={"Authorization": f"Bearer {args.api_key}"},
                                        limits=httpx.Limits(max_connections=args.concurrency))

    async def __call__(self, prompt: str, max_tokens: int, temperature: float) -> str:
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": temperature, **self.extra}
        for attempt in range(5):
            try:
                r = await self.client.post(self.url, json=body)
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"] or ""
            except (httpx.HTTPError, KeyError, ValueError):
                if attempt == 4:
                    raise
                await asyncio.sleep(2 ** attempt + random.random())
        return ""


async def run_jobs(args: argparse.Namespace, store: ShardStore, jobs: Iterable[tuple[object, object]],
                   worker: Callable[[Chat, object, object], Awaitable[list[dict]]]) -> None:
    """Run worker(chat, key, payload) for each job not yet in a shard; each call returns >= 1 row with that key."""
    done = store.done_keys()
    todo = [(k, p) for k, p in jobs if k not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(done)} done, {len(todo)} to do")
    if not todo:
        return
    chat = Chat(args)
    queue: asyncio.Queue = asyncio.Queue()
    for j in todo:
        queue.put_nowait(j)
    rows: list[dict] = []
    failed = 0
    last = start = time.monotonic()
    finished = 0

    async def loop():
        nonlocal failed, finished
        while True:
            try:
                key, payload = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                rows.extend(await worker(chat, key, payload))
            except Exception as e:  # noqa: BLE001 - one bad job must not stop an overnight run
                failed += 1
                print(f"job {key} failed: {e!r}")
            finished += 1

    def flush():
        nonlocal last
        store.write(rows[:])
        rows.clear()
        last = time.monotonic()

    tasks = [asyncio.create_task(loop()) for _ in range(args.concurrency)]
    try:
        while not all(t.done() for t in tasks):
            await asyncio.sleep(2)
            if time.monotonic() - last >= min(args.checkpoint_secs, 900) or len(rows) >= 50_000:
                el = time.monotonic() - start
                print(f"{finished}/{len(todo)} jobs, {finished / el:.1f}/s, {failed} failed")
                flush()
    finally:
        for t in tasks:
            t.cancel()
        flush()
        await chat.client.aclose()
    print(f"finished {finished} jobs in {time.monotonic() - start:.0f}s, {failed} failed (retried on next run)")
