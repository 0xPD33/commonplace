"""CIA World Factbook, final edition (factbook/factbook.json mirror, CC0; the CIA stopped it in 2026) → packbuild input.

One article per country or area, one section per Factbook category (Geography, People and Society, Economy, …),
lines "Field: value" or "Field — subfield: value". Articles are not linkable, so "Germany" keeps linking to
the Wikipedia article.

  python -m commonplace_pipeline.factbook --input data/raw/factbook/factbook.json-144d697* --out data/work/factbook
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .chunk import chunk_article
from .packwrite import PackWriter

TAG = re.compile(r"<[^>]+>")


def clean(s: str) -> str:
    return " ".join(TAG.sub(" ", s).replace("&nbsp;", " ").replace("&amp;", "&").split())


def lines(field: str, v) -> list[str]:
    if isinstance(v, dict) and "text" in v:
        return [f"{field}: {clean(v['text'])}"]
    if isinstance(v, dict):
        return [l for k, x in v.items() if k != "note" for l in lines(f"{field} — {k.strip()}", x)] + (
            [f"{field} (note): {clean(v['note']['text'] if isinstance(v['note'], dict) else v['note'])}"] if "note" in v else [])
    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    w = PackWriter(a.out)
    for f in sorted(Path(a.input).glob("*/*.json")):
        if f.parent.name == "meta":
            continue
        d = json.loads(f.read_text())
        cn = d.get("Government", {}).get("Country name", {})
        name = clean((cn.get("conventional short form") or cn.get("conventional long form") or {}).get("text", ""))
        if not name or name.lower() == "none":
            name = clean(d.get("Government", {}).get("Country name", {}).get("conventional long form", {}).get("text", "")) or f.stem
        md = []
        for section, fields in d.items():
            md += ["", f"## {section}", ""] + [f"- {l}" for k, v in fields.items() for l in lines(k.strip(), v) if l.split(": ", 1)[-1]]
        title = f"{name} (World Factbook)"
        w.add(title, chunk_article(title, "\n".join(md), None), popularity=f.stat().st_size // 1024,
              url_title=f"the-world-factbook/countries/{f.stem}")
    w.close()
    t = pq.read_table(f"{a.out}/articles.parquet")
    pq.write_table(t.append_column("linkable", pa.array([False] * t.num_rows)), f"{a.out}/articles.parquet", compression="zstd")


if __name__ == "__main__":
    main()
