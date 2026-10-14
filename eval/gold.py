#!/usr/bin/env python3
"""Gold articles: map each query's expected sources to articles in the installed library.

A query's `expected_sources` win. An entry is a title, resolved in every installed pack, or
`{"pack": "<pack id>", "title": "<title>"}` for one pack. Without them, the gold set is the
baseline's Wikipedia URLs plus its `wikipedia_articles` list and, unless `--wikipedia-only`,
the baseline URLs that open a page of another pack (Wikivoyage, Stack Exchange, Arch Wiki,
MedlinePlus, ...; the inverse of `pack::source_url`). Titles resolve by normalized title,
then by redirect. Writes eval/gold.jsonl for inspection.
"""

import argparse
import json
import re
import sqlite3
from urllib.parse import unquote, urlparse

from common import BASELINES, EVAL, LIBRARY, REPO, load_queries, norm_title

WIKI_PACKS = {
    "en.wikibooks.org": "wikibooks-en", "en.wikiversity.org": "wikiversity-en",
    "en.wikivoyage.org": "wikivoyage-en", "en.wikiquote.org": "wikiquote-en",
    "en.wiktionary.org": "wiktionary-en", "wikem.org": "wikem-en",
}
STACK_HOSTS = {"stackoverflow.com", "superuser.com", "serverfault.com", "askubuntu.com",
               "mathoverflow.net", "stackapps.com"}


def wiki_title(url):
    u = urlparse(url)
    if u.netloc.endswith("wikipedia.org") and u.path.startswith("/wiki/"):
        return unquote(u.path[len("/wiki/"):]).replace("_", " ")
    return None


def url_source(url):
    """The pack page a URL opens, as {"pack", "title" | "url_title"}; None when no pack holds it."""
    u = urlparse(url)
    host, path = u.netloc.removeprefix("www."), unquote(u.path)
    if pack := WIKI_PACKS.get(host):
        if path.startswith("/wiki/"):
            return {"pack": pack, "title": path[len("/wiki/"):].replace("_", " ")}
    elif host == "wiki.archlinux.org" and path.startswith("/title/"):
        return {"pack": "archwiki-en", "title": path[len("/title/"):].replace("_", " ")}
    elif host == "arxiv.org" and (m := re.match(r"/(?:abs|pdf)/([^v]+?)(?:v\d+)?(?:\.pdf)?$", path)):
        return {"pack": "arxiv-abs", "url_title": m.group(1)}
    elif host == "wwwnc.cdc.gov":
        return {"pack": "cdc-travel", "url_title": path.strip("/")}
    elif host == "medlineplus.gov" and path.endswith(".html"):
        return {"pack": "medlineplus", "url_title": path[1:-len(".html")]}
    elif host == "devdocs.io" and path.count("/") >= 2:
        return {"pack": "devdocs-en", "url_title": path.split("/", 2)[2].strip("/")}
    elif (host.endswith(".stackexchange.com") or host in STACK_HOSTS) and (
            m := re.match(r"/(?:questions|q)/(\d+)", path)):
        return {"pack": "stackexchange", "url_title": f"{host}/questions/{m.group(1)}"}
    return None


def entry(x):
    return {"title": x} if isinstance(x, str) else dict(x)


def label(e):
    return e.get("title") or e["url_title"]


def gold_sources(q, wikipedia_only=False):
    """The gold entries and where they came from: "expected_sources", "baseline" or None."""
    if q.get("expected_sources"):
        return [entry(x) for x in q["expected_sources"]], "expected_sources"
    path = BASELINES / f"{q['id']}.json"
    if not path.exists():
        return [], None
    b = json.loads(path.read_text())
    out = [{"title": t} for t in map(wiki_title, b.get("urls", [])) if t]
    out += [{"title": t} for t in b.get("wikipedia_articles", [])]
    if not wikipedia_only:
        out += [s for s in map(url_source, b.get("urls", [])) if s]
    seen, uniq = set(), []
    for e in out:
        key = (e.get("pack"), norm_title(label(e)) if "title" in e else e["url_title"])
        if key not in seen:
            seen.add(key)
            uniq.append(e)
    return uniq, "baseline"


class Resolver:
    def __init__(self, library=LIBRARY):
        self.dbs = []
        for meta in sorted((REPO / library / "packs").glob("*/meta.sqlite")):
            db = sqlite3.connect(f"file:{meta}?mode=ro", uri=True, check_same_thread=False)
            self.dbs.append((meta.parent.name, db))

    def resolve(self, e):
        """The first installed pack that holds the entry (only `e["pack"]` when it names one)."""
        for pack_id, db in self.dbs:
            if e.get("pack") and e["pack"] != pack_id:
                continue
            if "url_title" in e:
                row = db.execute("SELECT id, title FROM articles WHERE url_title = ? LIMIT 1",
                                 (e["url_title"],)).fetchone()
                via = "url"
            else:
                key = norm_title(e["title"])
                row = db.execute("SELECT id, title FROM articles WHERE title_norm = ? "
                                 "ORDER BY popularity DESC LIMIT 1", (key,)).fetchone()
                via = "title"
                if row is None:
                    row = db.execute("SELECT a.id, a.title FROM redirects r JOIN articles a "
                                     "ON a.id = r.article_id WHERE r.from_norm = ?", (key,)).fetchone()
                    via = "redirect"
            if row:
                return {"label": label(e), "pack_id": pack_id, "article_id": row[0],
                        "matched_title": row[1], "via": via}
        return None


def gold_for(q, resolver, wikipedia_only=False):
    entries, origin = gold_sources(q, wikipedia_only)
    resolved, unresolved = [], []
    for e in entries:
        r = resolver.resolve(e)
        (resolved if r else unresolved).append(r or label(e))
    unique = list({(r["pack_id"], r["article_id"]): r for r in resolved}.values())
    return {"id": q["id"], "origin": origin, "titles": [label(e) for e in entries],
            "gold": unique, "unresolved": unresolved}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", default="eval/dev.jsonl")
    ap.add_argument("--subset")
    ap.add_argument("--library", default=LIBRARY)
    ap.add_argument("--wikipedia-only", action="store_true", help="the old method: no gold from other packs' URLs")
    ap.add_argument("--out", default=str(EVAL / "gold.jsonl"))
    a = ap.parse_args()
    resolver = Resolver(a.library)
    rows = [gold_for(q, resolver, a.wikipedia_only) for q in load_queries(REPO / a.queries, a.subset)]
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_gold = sum(len(r["gold"]) for r in rows)
    n_titles = sum(len(r["titles"]) for r in rows)
    print(f"{len(rows)} queries, {n_titles} titles, {n_gold} resolved -> {a.out}")


if __name__ == "__main__":
    main()
