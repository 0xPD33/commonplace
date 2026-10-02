#!/usr/bin/env python3
"""Write catalog.json for a packs release from the split parts and the pack manifests.
Usage: scripts/catalog.py --dist DIR --library DIR --tag TAG --repo OWNER/REPO --out FILE
Lists every <pack_id>.pack.json under DIR. <library>/<pack_id>/manifest.json supplies the metadata."""
import argparse, hashlib, json, sys
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
    "stackexchange": "Questions and answers from 52 Stack Exchange sites.",
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
args = ap.parse_args()
dist, library = Path(args.dist), Path(args.library)
base = f"https://github.com/{args.repo}/releases/download/{args.tag}"

packs = []
for idx_path in sorted(dist.rglob("*.pack.json")):
    idx = json.loads(idx_path.read_text())
    pid = idx["pack_id"]
    m = json.loads((library / pid / "manifest.json").read_text())
    files = [{"name": idx_path.name, "bytes": idx_path.stat().st_size,
              "sha256": hashlib.sha256(idx_path.read_bytes()).hexdigest()}]
    for p in idx["parts"]:
        f = idx_path.parent / p["name"]
        if f.stat().st_size != p["bytes"]:
            sys.exit(f"{f}: size differs from {idx_path.name}")
        files.append({k: p[k] for k in ("name", "bytes", "sha256")})
    for f in files:
        f["url"] = f"{base}/{f['name']}"
    packs.append({
        "pack_id": pid,
        "title": m["title"],
        "description": DESCRIPTIONS.get(pid, m["attribution"]),
        "pack_type": m["pack_type"],
        "snapshot": m["snapshot_date"],
        "license": m["license"],
        "replaces": m["replaces"],
        "recommended": pid in RECOMMENDED,
        "download_bytes": sum(f["bytes"] for f in files),
        "installed_bytes": m["size_bytes"],
        "files": files,
    })
packs.sort(key=lambda p: (not p["recommended"], p["pack_id"]))
doc = {"catalog_version": 1, "tag": args.tag, "repo": args.repo, "packs": packs}
Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")
