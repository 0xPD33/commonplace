#!/usr/bin/env python3
"""Gold articles: map each query's expected sources to article ids in the installed library.

A query's `expected_sources` (hand-annotated titles) win. Without them, the gold set is the
baseline's Wikipedia URLs plus its `wikipedia_articles` list. Titles resolve by normalized
title, then by redirect, in every installed pack. Writes eval/gold.jsonl for inspection.
"""

import argparse
import json
import sqlite3
from urllib.parse import unquote, urlparse

from common import BASELINES, EVAL, LIBRARY, REPO, load_queries, norm_title


def wiki_title(url):
    u = urlparse(url)
    if u.netloc.endswith("wikipedia.org") and u.path.startswith("/wiki/"):
        return unquote(u.path[len("/wiki/"):]).replace("_", " ")
    return None


def gold_titles(q):
    if q.get("expected_sources"):
        return list(q["expected_sources"]), "expected_sources"
    path = BASELINES / f"{q['id']}.json"
    if not path.exists():
        return [], None
    b = json.loads(path.read_text())
    titles = [t for t in map(wiki_title, b.get("urls", [])) if t] + b.get("wikipedia_articles", [])
    seen, out = set(), []
    for t in titles:
        if norm_title(t) not in seen:
            seen.add(norm_title(t))
            out.append(t)
    return out, "baseline"


class Resolver:
    def __init__(self, library=LIBRARY):
        self.dbs = []
        for meta in sorted((REPO / library / "packs").glob("*/meta.sqlite")):
            db = sqlite3.connect(f"file:{meta}?mode=ro", uri=True, check_same_thread=False)
            self.dbs.append((meta.parent.name, db))

    def resolve(self, title):
        key = norm_title(title)
        for pack_id, db in self.dbs:
            row = db.execute("SELECT id, title FROM articles WHERE title_norm = ? "
                             "ORDER BY popularity DESC LIMIT 1", (key,)).fetchone()
            via = "title"
            if row is None:
                row = db.execute("SELECT a.id, a.title FROM redirects r JOIN articles a "
                                 "ON a.id = r.article_id WHERE r.from_norm = ?", (key,)).fetchone()
                via = "redirect"
            if row:
                return {"title": title, "pack_id": pack_id, "article_id": row[0],
                        "matched_title": row[1], "via": via}
        return None


def gold_for(q, resolver):
    titles, origin = gold_titles(q)
    resolved = [r for r in map(resolver.resolve, titles) if r]
    unique = list({(r["pack_id"], r["article_id"]): r for r in resolved}.values())
    return {"id": q["id"], "origin": origin, "titles": titles, "gold": unique,
            "unresolved": [t for t in titles
                           if not any(r["title"] == t for r in resolved)]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queries", default="eval/dev.jsonl")
    ap.add_argument("--subset")
    ap.add_argument("--library", default=LIBRARY)
    ap.add_argument("--out", default=str(EVAL / "gold.jsonl"))
    a = ap.parse_args()
    resolver = Resolver(a.library)
    rows = [gold_for(q, resolver) for q in load_queries(REPO / a.queries, a.subset)]
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    n_gold = sum(len(r["gold"]) for r in rows)
    n_titles = sum(len(r["titles"]) for r in rows)
    print(f"{len(rows)} queries, {n_titles} titles, {n_gold} resolved -> {a.out}")


if __name__ == "__main__":
    main()
