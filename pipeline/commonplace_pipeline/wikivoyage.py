"""English Wikivoyage from the Wikimedia XML dump (pages-articles + page_props for QIDs).

Wikitext becomes the markdown that chunk.py parses: headings, pipe tables, and list items.
Listing templates ({{see}}, {{eat}}, {{sleep}}, ...) become one list line each.
"""

from __future__ import annotations

import argparse
import bz2
import gzip
import re
import xml.etree.ElementTree as ET
from multiprocessing import Pool

import wikitextparser as wtp

from .chunk import chunk_article
from .packwrite import PackWriter

LISTINGS = {"see", "do", "buy", "eat", "drink", "sleep", "go", "listing", "marker", "vcard"}
UNITS = {"km": "km", "mi": "mi", "m": "m", "ft": "ft", "km2": "km²", "mile": "miles", "c": "°C", "f": "°F"}
FIRST_ARG = {"lang", "iata", "nowrap", "quote", "phone", "station", "small", "smaller", "big", "w", "wikipedia"}
DISAMB = {"disamb", "disambiguation", "dab"}
DROP_RE = re.compile(r"<ref[^>/]*/>|<(ref|gallery|maplink|mapframe)[^>]*>.*?</\1>|\[\[(?:Category|[a-z]{2,3}(?:-[a-z]+)?):[^\]]*\]\]",
                     re.S | re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
HEADING_RE = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$")
QID_RE = re.compile(r"\((\d+),'wikibase_item','(Q\d+)'")
# Italic indented lines in the lead are hatnotes ("For the town in Spain, see ...").
HATNOTE_RE = re.compile(r"^:+\s*''.*''\s*$", re.M)


def plain(s: str) -> str:
    return wtp.parse(s).plain_text(replace_templates=render_template, replace_tables=render_table).strip()


def arg(t: wtp.Template, name: str) -> str:
    a = t.get_arg(name)
    return plain(a.value) if a else ""


def render_listing(t: wtp.Template) -> str:
    name, alt = arg(t, "name"), arg(t, "alt")
    head = f"{name} ({alt})" if name and alt else name
    if t.normal_name().lower() == "marker":
        return head
    parts = [head] + [x for x in (arg(t, "address"), arg(t, "directions") and f"({arg(t, 'directions')})") if x]
    out = ", ".join(p for p in parts if p)
    for label, key in (("Phone", "phone"), ("Hours", "hours"), ("Check-in", "checkin"), ("Check-out", "checkout"),
                       ("Price", "price")):
        v = arg(t, key)
        if v:
            out += f". {label}: {v}"
    content = arg(t, "content")
    if content:
        out += ". " + content
    return " ".join(out.split())


def render_template(t: wtp.Template) -> str:
    n = t.normal_name().lower().replace("_", " ")
    pos = [a for a in t.arguments if a.positional]
    first = plain(pos[0].value) if pos else ""
    if n in LISTINGS:
        return render_listing(t)
    if n in UNITS and first:
        return f"{first} {UNITS[n]}"
    if n == "convert" and len(pos) >= 2:
        return f"{first} {plain(pos[1].value)}"
    if n in FIRST_ARG:
        return first
    if n == "unesco":
        return "UNESCO World Heritage Site"
    if n == "infobox" and len(pos) >= 2:
        return f"\n{first}:\n{plain(pos[1].value)}\n"
    # Currency templates are named by ISO code: {{eur|5}} -> 5 EUR.
    if len(n) == 3 and n.isalpha() and first and first[0].isdigit():
        return f"{first} {n.upper()}"
    return ""


def render_table(table: wtp.Table) -> str:
    try:
        rows = table.data()
    except (IndexError, ValueError):
        return ""
    lines = []
    for r in rows:
        cells = [" ".join(plain(c or "").replace("|", "/").split()) for c in r]
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return "\n" + "\n".join(lines) + "\n"


def drop_files(wikitext: str) -> str:
    """Remove [[File:...]] links, whose captions may nest other links (regex cannot match those)."""
    spans = sorted(w.span for w in wtp.parse(wikitext).wikilinks
                   if w.title.strip().lower().startswith(("file:", "image:")))
    out, pos = [], 0
    for b, e in spans:
        if b >= pos:
            out.append(wikitext[pos:b])
            pos = e
    return "".join(out) + wikitext[pos:]


def to_markdown(wikitext: str) -> str:
    lead, sep, rest = wikitext.partition("\n==")
    text = plain(BR_RE.sub(" ", DROP_RE.sub("", drop_files(HATNOTE_RE.sub("", lead) + sep + rest))))
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if m := HEADING_RE.match(s):
            out.append(f"{'#' * len(m.group(1))} {m.group(2)}")
        elif s[:1] in ("*", "#", ";"):
            body = s.lstrip("*#;: ").strip()
            if s.startswith(";") and " : " in body:
                term, _, desc = body.partition(" : ")
                body = f"{term.strip()}: {desc.strip()}"
            if body:
                out.append(f"- {body}")
        elif s.startswith(":"):
            if body := s.lstrip(": ").strip():
                out.append(body)
        else:
            out.append(s)
    return "\n".join(out)


def process(page: tuple[str, str]) -> list[tuple[str, str]]:
    title, text = page
    return chunk_article(title, to_markdown(text), None)


def iter_pages(path: str):
    """Yield (page_id, title, wikitext) for non-redirect, non-disambiguation main-namespace pages."""
    for _, el in ET.iterparse(bz2.open(path), events=("end",)):
        if not el.tag.endswith("}page"):
            continue
        get = lambda name: el.find(f"{{*}}{name}")  # noqa: E731
        text_el = el.find("{*}revision/{*}text")
        text = (text_el.text if text_el is not None else None) or ""
        if get("ns").text == "0" and get("redirect") is None and get("title").text != "Main Page":
            names = {t.lower().strip().replace("_", " ") for t in re.findall(r"\{\{\s*([^|}]+)", text[:20000])}
            if not names & DISAMB:
                yield int(get("id").text), get("title").text, text
        el.clear()


def load_qids(path: str) -> dict[int, str]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        return {int(p): q for line in f if "wikibase_item" in line for p, q in QID_RE.findall(line)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True, help="enwikivoyage-*-pages-articles.xml.bz2")
    ap.add_argument("--page-props", required=True, help="enwikivoyage-*-page_props.sql.gz")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="max articles (0 = all)")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    qids = load_qids(args.page_props)
    pages = []
    for pid, title, text in iter_pages(args.dump):
        # ponytail: no Wikivoyage pageviews on disk; guide length stands in for importance.
        pages.append((pid, title, text, len(text) // 1000))
        if args.limit and len(pages) >= args.limit:
            break
    w = PackWriter(args.out)
    with Pool(args.workers) as pool:
        for (pid, title, _, pop), chunks in zip(pages, pool.imap(process, [(p[1], p[2]) for p in pages], chunksize=16)):
            w.add(title, chunks, qid=qids.get(pid), popularity=pop)
    w.close()


if __name__ == "__main__":
    main()
