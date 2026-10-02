"""CDC Travelers' Health destination pages (U.S. government work, public domain) → packbuild input.

One article per destination: travel health notices, the vaccine table, diseases, and safety advice.

  python -m commonplace_pipeline.cdc_travel fetch --out data/raw/cdc-travel    # 244 pages, 20 s apart (robots.txt)
  python -m commonplace_pipeline.cdc_travel build --input data/raw/cdc-travel --out data/work/cdc-travel
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import httpx

from .chunk import chunk_article
from .packwrite import PackWriter
from .zim import page_markdown

BASE = "https://wwwnc.cdc.gov"
CRAWL_DELAY_S = 20  # wwwnc.cdc.gov/robots.txt
UA = "Mozilla/5.0 (compatible; commonplace-packbuild; offline travel-health pack)"


def cmd_fetch(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=60) as c:
        slugs = sorted(set(re.findall(r'href="/travel/destinations/traveler/none/([^"/]+)"', c.get(f"{BASE}/travel/destinations/list").text)))
        print(f"{len(slugs)} destinations")
        for s in slugs:
            f = out / f"{s}.html"
            if f.exists():
                continue
            for attempt in range(5):
                time.sleep(CRAWL_DELAY_S * (attempt + 1))
                try:
                    r = c.get(f"{BASE}/travel/destinations/traveler/none/{s}")
                    r.raise_for_status()
                    break
                except httpx.HTTPError as e:
                    print(f"{s}: {e}", flush=True)
            else:
                raise SystemExit(f"{s}: gave up after 5 attempts")
            f.write_bytes(r.content)
            print(s, flush=True)


def cmd_build(a) -> None:
    w = PackWriter(a.out)
    for f in sorted(Path(a.input).glob("*.html")):
        html = f.read_bytes()
        name = re.search(rb"<h3>([^<]+)", html)
        country = (name.group(1).decode().strip() if name else f.stem.replace("-", " ").title())
        chunks = chunk_article(country, page_markdown(html, root="#destination"), None)
        w.add(f"{country}: travel health (CDC)", chunks, popularity=len(html) // 1024,
              url_title=f"travel/destinations/traveler/none/{f.stem}")
    w.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--out", required=True)
    b = sub.add_parser("build")
    b.add_argument("--input", required=True)
    b.add_argument("--out", required=True)
    a = ap.parse_args()
    {"fetch": cmd_fetch, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
