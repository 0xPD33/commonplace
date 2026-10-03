#!/usr/bin/env python3
"""Write catalog.json for a packs release from the pack files and the pack manifests.
Usage: scripts/catalog.py --dist DIR --library DIR --tag TAG --repo NAMESPACE/NAME --out FILE [--readme FILE]
Lists every <pack_id>.tar in DIR. <library>/<pack_id>/manifest.json supplies the metadata.
--repo is the Hugging Face dataset repo; the files live in its folder TAG. --readme writes the dataset card."""
import argparse, hashlib, json
from pathlib import Path

RECOMMENDED = {"enwiki-core", "ling3-tiny", "wikidata-facts"}
DESCRIPTIONS = {
    "enwiki-core": "The 2 million most useful English Wikipedia articles.",
    "enwiki": "All of English Wikipedia. Replaces the starter pack.",
    "simplewiki": "Simple English Wikipedia.",
    "wikidata-facts": "Structured facts from Wikidata.",
    "ling3-tiny": "Fast answer model (Ling 3.0 tiny).",
    "lfm25-1.2b": "Small answer model (LFM2.5 1.2B).",
    "lfm25-8b-a1b": "Mid-size answer model (LFM2.5 8B-A1B).",
    "qwen36-35b-a3b": "Deep answer model for Think harder (Qwen3.6 35B-A3B).",
    "arxiv-abs": "Abstracts of arXiv papers.",
    "stackexchange": "Questions and answers from 51 Stack Exchange sites.",
    "textbooks-en": "Open textbooks from Pressbooks and LibreTexts.",
    "openstax": "OpenStax college textbooks.",
    "devdocs-en": "Programming documentation from DevDocs.",
    "archwiki-en": "ArchWiki.",
    "wikem-en": "WikEM emergency medicine.",
    "medlineplus": "MedlinePlus health topics.",
    "cdc-travel": "CDC travel health advice by destination.",
    "factbook": "CIA World Factbook, final edition.",
    "wikibooks-en": "English Wikibooks.",
    "wikiquote-en": "English Wikiquote.",
    "wikiversity-en": "English Wikiversity.",
    "wikivoyage-en": "English Wikivoyage travel guides.",
    "wiktionary-en": "English Wiktionary dictionary.",
}

ap = argparse.ArgumentParser()
for a in ("dist", "library", "tag", "repo", "out"):
    ap.add_argument("--" + a, required=True)
ap.add_argument("--readme")
args = ap.parse_args()
dist, library = Path(args.dist), Path(args.library)
base = f"https://huggingface.co/datasets/{args.repo}/resolve/main/{args.tag}"

packs = []
credits = {}
for tar in sorted(dist.glob("*.tar")):
    pid = tar.stem
    m = json.loads((library / pid / "manifest.json").read_text())
    credits[pid] = m["attribution"]
    with tar.open("rb") as f:
        sha256 = hashlib.file_digest(f, "sha256").hexdigest()
    size = tar.stat().st_size
    files = [{"name": tar.name, "bytes": size, "sha256": sha256, "url": f"{base}/{tar.name}?download=true"}]
    packs.append({
        "pack_id": pid,
        "title": m["title"],
        "description": DESCRIPTIONS.get(pid, m["attribution"]),
        "pack_type": m["pack_type"],
        "snapshot": m["snapshot_date"],
        "license": m["license"],
        "replaces": m["replaces"],
        "recommended": pid in RECOMMENDED,
        "download_bytes": size,
        "installed_bytes": m["size_bytes"],
        "files": files,
    })
packs.sort(key=lambda p: (not p["recommended"], p["pack_id"]))
doc = {"catalog_version": 1, "tag": args.tag, "repo": args.repo, "packs": packs}
Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")

if args.readme:
    cell = lambda s: " ".join(s.split()).replace("|", "/")
    human = lambda n: f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"
    rows = "\n".join(
        f"| `{args.tag}/{p['files'][0]['name']}` | {human(p['download_bytes'])} | {cell(p['license'])} | {cell(credits[p['pack_id']])} | `{p['files'][0]['sha256']}` |"
        for p in packs
    )
    Path(args.readme).write_text(f"""---
license: other
pretty_name: Commonplace packs
tags:
- offline
- retrieval
- wikipedia
- commonplace
---

# Commonplace packs

These files are the knowledge packs and model packs of the Commonplace Android app. The app works offline and has no network permission. You download the files with a browser and install them with the app.

Release: `{args.tag}`. Each pack is one `.tar` file in the folder `{args.tag}/`. The folder also holds `catalog.json` and `SHA256SUMS`.

## Install

- On a phone: open Commonplace, go to Library, tap a pack under Get more, and tap Download. Then tap Install from Downloads. The app checks every byte (SHA-256) during the import.
- On a computer: run `tar -xf <pack_id>.tar -C data/library/packs`.

## Packs

| File | Size | License | Credit | SHA-256 |
|---|---|---|---|---|
{rows}

## Licenses

Each pack keeps the license of its source, so this card uses `license: other`. Every pack contains its own `NOTICE.txt` with the credit, the license name and the full license texts. Read the `NOTICE.txt` of a pack before you reuse it.
""")
