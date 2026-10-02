"""Fact cards: short standalone facts per (article, section), each citing its passages.

The prompt numbers the section's passages {p1}..{pN}; the parser maps those back to global passage ids.
A fact is dropped when a number, date or capitalized name in it is not literally in its cited passages
(the article title and section path also count as source). Output: <out-dir>/cards.parquet
(passage_id uint32 = first cited passage, fact str, source_ids str "12,13").
"""

from __future__ import annotations

import argparse
import asyncio
import re
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .llmgen import ShardStore, add_endpoint_args, load_passages, load_popular, run_jobs, strip_numbering

MAX_FACT_WORDS, MAX_GROUP_PASSAGES = 30, 6
PROMPT = """Extract the most useful facts from this Wikipedia section as short standalone statements.
Each fact: at most 30 words, names its subject explicitly (no "it"/"he"), keeps exact numbers,
units and dates as written, and ends with the ids of the passages it came from, like {{p1}}.
Prefer facts people would ask about: definitions, causes, comparisons, quantities, dates,
locations, outcomes. Skip trivia and citations. Output 3–10 lines.

Article: {title}
Section: {section}
Passages:
{passages}"""
SCHEMA = pa.schema([
    ("key", pa.string()), ("passage_id", pa.uint32()), ("fact", pa.string()), ("source_ids", pa.string()),
    ("kept", pa.bool_()), ("reason", pa.string()),
])

CITES_RE = re.compile(r"((?:\s*[{\[(]\s*p\d+(?:\s*[,;]\s*p?\d+)*\s*[}\])])+)[\s.]*$")
JSON_LINE_RE = re.compile(r'^\{?\s*"?p(\d+)"?\s*:\s*"?(.*?)"?\s*\}?,?$')  # some models answer {"p1": "fact"}
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
THOUSANDS_RE = re.compile(r"(?<=\d)[,\u00a0\u202f\u2009](?=\d{3}(?!\d))")
SPACES = str.maketrans({"\u00a0": " ", "\u202f": " ", "\u2009": " ", "\u2010": "-", "\u2011": "-"})
WORD_RE = re.compile(r"[^\W_][\w'’.-]*[^\W_]|[^\W_]")
SENT_START_RE = re.compile(r"(?:^|[.!?:;]\s+|[\"“(]\s*)$")
COMMON_INITIAL = {
    "The", "A", "An", "In", "On", "At", "By", "For", "From", "During", "After", "Before", "As", "Since", "Its", "It",
    "This", "These", "Those", "There", "Many", "Most", "Some", "Both", "Each", "Unlike", "According", "Although",
    "While", "When", "Under", "Over", "About", "Around", "Between", "Today", "Only", "One", "Two", "Three",
    "More", "Less", "Several", "Later", "Early", "Following", "Despite", "Because", "If", "Once", "Like", "Such",
    "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Additional", "Other", "Another", "New", "All", "No",
}


def clean(s: str) -> str:
    return THOUSANDS_RE.sub("", s).translate(SPACES)


def norm_num(s: str) -> str:
    return THOUSANDS_RE.sub("", s).rstrip(".,")


def words_of(text: str) -> set[str]:
    out: set[str] = set()
    for w in WORD_RE.findall(text):
        w = re.sub(r"['’]s$", "", w).rstrip(".")
        out.add(w)
        out.update(p for p in re.split(r"[-.'’]", w) if p)
    return out


def unsupported(fact: str, source: str) -> list[str]:
    """Numbers and capitalized words in fact that do not appear in source."""
    fact, source = clean(fact), clean(source)
    src_nums = {norm_num(n) for n in NUM_RE.findall(source)}
    src_words = words_of(source)
    src_lower = {w.lower() for w in src_words}
    missing = [n for n in NUM_RE.findall(fact) if norm_num(n) not in src_nums]
    for m in WORD_RE.finditer(fact):
        w = re.sub(r"['’]s$", "", m.group()).rstrip(".")
        if not w[0].isupper() or w in src_words:
            continue
        if SENT_START_RE.search(fact[: m.start()]) and (w in COMMON_INITIAL or w.lower() in src_lower or w.endswith("ly")):
            continue
        parts = [p for p in re.split(r"[-'’]", w) if p]
        if len(parts) > 1 and all(p in src_words or not p[0].isupper() for p in parts):
            continue
        missing.append(w)
    return missing


def parse_facts(out: str, local_ids: list[int], context: str, texts: dict[int, str]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for line in out.splitlines():
        line = strip_numbering(line)
        if j := JSON_LINE_RE.match(line):
            line = f"{j.group(2)} {{p{j.group(1)}}}"
        m = CITES_RE.search(line)
        if not m:
            if line and not line.endswith(":") and len(line.split()) >= 4:
                rows.append({"fact": line, "source_ids": "", "kept": False, "reason": "no_cite"})
            continue
        fact = line[: m.start()].strip()
        cited = [int(x) for x in re.findall(r"\d+", m.group(1))]
        if not fact or fact.lower() in seen:
            continue
        seen.add(fact.lower())
        if any(c < 1 or c > len(local_ids) for c in cited):
            rows.append({"fact": fact, "source_ids": "", "kept": False, "reason": "bad_cite"})
            continue
        ids = list(dict.fromkeys(local_ids[c - 1] for c in cited))
        row = {"fact": fact, "passage_id": ids[0], "source_ids": ",".join(map(str, ids)), "kept": True, "reason": ""}
        if len(fact.split()) > MAX_FACT_WORDS:
            row.update(kept=False, reason="too_long")
        elif miss := unsupported(fact, context + "\n" + "\n".join(texts[i] for i in ids)):
            row.update(kept=False, reason="unsupported: " + " ".join(miss))
        rows.append(row)
    return rows


def groups(work: Path, n_articles: int) -> list[tuple[str, dict]]:
    """(key, {title, section, passage_ids}) per run of passages sharing a section, at most MAX_GROUP_PASSAGES each."""
    top = load_popular(work, n_articles).to_pylist()
    ids = [p for a in top for p in range(a["first_passage"], a["first_passage"] + a["n_passages"])]
    sections = pq.read_table(work / "passages.parquet", columns=["passage_id", "section_path"],
                             filters=[("passage_id", "in", ids)])
    sec = dict(zip(*sections.to_pydict().values()))
    out = []
    for a in top:
        run: list[int] = []
        for p in range(a["first_passage"], a["first_passage"] + a["n_passages"]):
            if run and (sec[p] != sec[run[0]] or len(run) == MAX_GROUP_PASSAGES):
                out.append((f"{a['article_id']}:{run[0]}", {"title": a["title"], "section": sec[run[0]], "ids": run}))
                run = []
            run.append(p)
        if run:
            out.append((f"{a['article_id']}:{run[0]}", {"title": a["title"], "section": sec[run[0]], "ids": run}))
    return out


def merge(work: Path, store: ShardStore, out: Path) -> None:
    """Re-run the hallucination filter on every cited fact, so filter changes need no new generation."""
    t = store.read_all()
    n_sections = len(set(t["key"].to_pylist()))
    facts = t.filter(pc.is_valid(t["fact"])).to_pylist()
    recheck = [f for f in facts if f["kept"] or f["reason"].startswith("unsupported")]
    ids = sorted({int(i) for f in recheck for i in f["source_ids"].split(",")})
    passages = load_passages(work, ids)
    titles = dict(zip(*pq.read_table(work / "articles.parquet", columns=["article_id", "title"]).to_pydict().values()))
    for f in recheck:
        src = [passages[int(i)] for i in f["source_ids"].split(",")]
        context = f"{titles[src[0]['article_id']]}\n{src[0]['section_path']}\n" + "\n".join(p["text"] for p in src)
        miss = unsupported(f["fact"], context)
        f["kept"], f["reason"] = not miss, "unsupported: " + " ".join(miss) if miss else ""
    kept = [f for f in facts if f["kept"]]
    reasons = Counter(f["reason"].split(":")[0] for f in facts if not f["kept"])
    cited = len(facts) - reasons["no_cite"] - reasons["bad_cite"]
    pq.write_table(pa.Table.from_pylist(sorted(kept, key=lambda f: f["passage_id"]), SCHEMA).select(["passage_id", "fact", "source_ids"]),
                   out / "cards.parquet", compression="zstd")
    dropped = len(facts) - len(kept)
    line = (f"cards.parquet: {len(kept)} facts from {n_sections} sections; "
            f"parsed {len(facts)}, dropped {dropped} ({dropped / max(len(facts), 1):.1%}); "
            f"hallucination filter dropped {reasons['unsupported']}/{cited} cited ({reasons['unsupported'] / max(cited, 1):.1%}); "
            f"reasons {dict(reasons)}")
    print(line)
    with open(out / "cards.log", "a") as f:
        f.write(line + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    add_endpoint_args(ap)
    ap.add_argument("--articles", type=int, default=200_000, help="top-N articles by popularity")
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--temperature", type=float, default=0.2)
    args = ap.parse_args()
    work = Path(args.work)
    out = Path(args.out_dir or args.work)
    store = ShardStore(out, "c", SCHEMA)
    if not args.merge_only:
        done = store.done_keys()
        pending = [g for g in groups(work, args.articles) if g[0] not in done][: args.limit or None]
        texts = {p: r["text"] for p, r in load_passages(work, [i for _, g in pending for i in g["ids"]]).items()}

        async def worker(chat, key, g):
            listing = "\n".join(f"{{p{i + 1}}} {texts[p]}" for i, p in enumerate(g["ids"]))
            prompt = PROMPT.format(title=g["title"], section=g["section"] or "Introduction", passages=listing)
            reply = await chat(prompt, args.max_tokens, args.temperature)
            rows = parse_facts(reply, g["ids"], f"{g['title']}\n{g['section']}", texts)
            return [{"key": key, **r} for r in rows] or [{"key": key, "fact": None, "kept": False, "reason": "empty"}]

        asyncio.run(run_jobs(args, store, pending, worker))
    merge(work, store, out)


if __name__ == "__main__":
    main()
