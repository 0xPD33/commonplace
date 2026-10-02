"""MedlinePlus health topics (NLM XML). The English topic summaries are public domain.

One article per English health topic; section_path holds its topic groups.
"""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from html import unescape

from .chunk import first_sentence, pack_blocks
from .packwrite import PackWriter

BLOCK_END_RE = re.compile(r"</(p|ul|ol|h\d|div)>|<(p|ul|ol|h\d|div)[^>]*>", re.I)
LINE_END_RE = re.compile(r"</li>|<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def summary_blocks(html: str, institute: str) -> list[str]:
    text = unescape(TAG_RE.sub("", LINE_END_RE.sub("\n", BLOCK_END_RE.sub("\n\n", html))))
    blocks = ["\n".join(" ".join(ln.split()) for ln in b.splitlines() if ln.strip()) for b in text.split("\n\n")]
    # The last paragraph usually names the source institute ("NIH: National Cancer Institute").
    return [b for b in blocks if b and not (institute and b.endswith(institute) and len(b.split()) < 15)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="mplus_topics_<date>.xml")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    w = PackWriter(args.out)
    for t in ET.parse(args.input).getroot().iter("health-topic"):
        if t.get("language") != "English":
            continue
        inst = t.find("primary-institute")
        blocks = summary_blocks(t.findtext("full-summary") or "", inst.text.strip() if inst is not None and inst.text else "")
        if not blocks:
            continue
        oneliner = first_sentence(blocks[0])
        also = [a.text.strip() for a in t.findall("also-called") if a.text]
        if also:
            blocks[0] = f"Also called: {', '.join(also)}.\n{blocks[0]}"
        groups = "; ".join(g.text.strip() for g in t.findall("group") if g.text)
        chunks = [(groups, p) for p in pack_blocks(blocks)]
        w.add(t.get("title"), chunks, url_title=t.get("url").rsplit("/", 1)[-1].removesuffix(".html"), oneliner=oneliner)
    w.close()


if __name__ == "__main__":
    main()
