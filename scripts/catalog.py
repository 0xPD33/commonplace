#!/usr/bin/env python3
"""Write catalog.json for a packs release from the pack files and the pack manifests.
Usage: scripts/catalog.py --dist DIR --library DIR --tag TAG --repo NAMESPACE/NAME --out FILE [--readme FILE] [--base FILE] [--bundle SPEC]...
Lists every <pack_id>.tar in DIR. <library>/<pack_id>/manifest.json supplies the metadata.
--base keeps the entries of an earlier catalog whose files are not in DIR, so one new pack can join a released set.
--bundle 'ID|TITLE|DESCRIPTION|PACK_ID PACK_ID...' (repeatable) lists the bundle file commonplace-ID.tar when DIR has it.
--repo is the Hugging Face dataset repo; the files live in its folder TAG. --readme writes the dataset card."""
import argparse, hashlib, json
from pathlib import Path

RECOMMENDED = {"enwiki-core", "ling3-tiny", "wikidata-facts"}
RECOMMENDED_BUNDLES = {"starter", "starter-small"}
# Packs that run on one chip only (Android's Build.SOC_MODEL); the app hides them on other phones.
CHIPS = {"gemma4-e2b-npu": "Tensor G5"}
DESCRIPTIONS = {
    "enwiki-core": "The 2 million most useful English Wikipedia articles.",
    "enwiki": "All of English Wikipedia. Replaces the starter pack.",
    "simplewiki": "Simple English Wikipedia.",
    "wikidata-facts": "Structured facts from Wikidata.",
    "ling3-tiny": "Fast answer model (Ling 3.0 tiny).",
    "minicpm5-2b": "Small answer model (MiniCPM5 2B).",
    "voice-en": "Speak your question instead of typing it. English only.",
    "gemma4-e2b-npu": "Fastest answers on a Pixel 10: Gemma 4 E2B on the Tensor G5 chip. Becomes the answer model.",
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
ap.add_argument("--base")
ap.add_argument("--bundle", action="append", default=[])
args = ap.parse_args()
dist, library = Path(args.dist), Path(args.library)
base = f"https://huggingface.co/datasets/{args.repo}/resolve/main/{args.tag}"
bundle_specs = [b.split("|") for b in args.bundle]
bundle_files = {f"commonplace-{b[0]}.tar" for b in bundle_specs}


def file_entry(tar):
    with tar.open("rb") as f:
        sha256 = hashlib.file_digest(f, "sha256").hexdigest()
    return {"name": tar.name, "bytes": tar.stat().st_size, "sha256": sha256, "url": f"{base}/{tar.name}?download=true"}


bundles = []
for bid, title, description, members in bundle_specs:
    tar = dist / f"commonplace-{bid}.tar"
    if not tar.exists():
        continue
    ids = members.split()
    file = file_entry(tar)
    bundles.append({
        "bundle_id": bid,
        "title": title,
        "description": description,
        "pack_ids": ids,
        "members": [{"pack_id": i, "title": json.loads((library / i / "manifest.json").read_text())["title"]} for i in ids],
        "recommended": bid in RECOMMENDED_BUNDLES,
        "download_bytes": file["bytes"],
        "installed_bytes": sum(json.loads((library / i / "manifest.json").read_text())["size_bytes"] for i in ids),
        "files": [file],
    })

packs = []
credits = {}
for tar in sorted(dist.glob("*.tar")):
    if tar.name in bundle_files:
        continue
    pid = tar.stem
    m = json.loads((library / pid / "manifest.json").read_text())
    credits[pid] = m["attribution"]
    files = [file_entry(tar)]
    size = files[0]["bytes"]
    packs.append({
        "pack_id": pid,
        "title": m["title"],
        "description": DESCRIPTIONS.get(pid, m["attribution"]),
        "pack_type": m["pack_type"],
        "snapshot": m["snapshot_date"],
        "license": m["license"],
        "attribution": m["attribution"],
        "replaces": m["replaces"],
        "recommended": pid in RECOMMENDED,
        "download_bytes": size,
        "installed_bytes": m["size_bytes"],
        "files": files,
    } | ({"chip": CHIPS[pid]} if pid in CHIPS else {}))
if args.base and Path(args.base).exists():
    base = json.loads(Path(args.base).read_text())
    built = {p["pack_id"] for p in packs}
    packs += [p for p in base.get("packs", []) if p["pack_id"] not in built]
    # Keep the published order of the bundles; a rebuilt bundle takes its old place.
    built = {b["bundle_id"]: b for b in bundles}
    bundles = [built.pop(b["bundle_id"], b) for b in base.get("bundles", [])] + list(built.values())
    for p in packs:
        manifest = library / p["pack_id"] / "manifest.json"
        credits.setdefault(p["pack_id"], json.loads(manifest.read_text())["attribution"] if manifest.exists() else p["attribution"])
packs.sort(key=lambda p: (not p["recommended"], p["pack_id"]))
doc = {"catalog_version": 1, "tag": args.tag, "repo": args.repo, "bundles": bundles, "packs": packs}
Path(args.out).write_text(json.dumps(doc, indent=2) + "\n")

if args.readme:
    cell = lambda s: " ".join(s.split()).replace("|", "/")
    human = lambda n: f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"
    rows = "\n".join(
        f"| `{args.tag}/{p['files'][0]['name']}` | {human(p['download_bytes'])} | {cell(p['license'])} | {cell(credits[p['pack_id']])} | `{p['files'][0]['sha256']}` |"
        for p in packs
    )
    bundle_rows = "\n".join(
        f"| `{args.tag}/{b['files'][0]['name']}` | {cell(b['title'])}: {', '.join(f'`{i}`' for i in b['pack_ids'])} | {human(b['download_bytes'])} | `{b['files'][0]['sha256']}` |"
        for b in bundles
    )
    member = lambda i: json.loads((library / i / "manifest.json").read_text())
    members = list(dict.fromkeys(i for b in bundles for i in b["pack_ids"]))
    member_rows = "\n".join(f"| `{i}` | {cell(member(i)['license'])} | {cell(member(i)['attribution'])} |" for i in members)
    own_file = [f"`{i}`" for i in members if i in {p["pack_id"] for p in packs}]
    bundle_section = f"""## Bundles

A bundle is one `.tar` file with several packs. It only saves downloads: every pack inside is a normal pack. A pack inside a bundle has no file of its own{", except " + ", ".join(own_file) if own_file else ""}.

| File | Contents | Size | SHA-256 |
|---|---|---|---|
{bundle_rows}

Packs inside the bundles:

| Pack | License | Credit |
|---|---|---|
{member_rows}

""" if bundles else ""
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

Release: `{args.tag}`. Each pack is one `.tar` file in the folder `{args.tag}/`, and each bundle is one `.tar` file with several packs. The folder also holds `catalog.json` and `SHA256SUMS`.

## Install

- On a phone: open Commonplace, go to Library, tap a bundle or a pack under Get more, and tap Download. Then tap Install from Downloads. The app checks every byte (SHA-256) during the import. The import needs about twice the file size in free space. You can delete the download afterwards.
- On a computer: run `tar -xf <file>.tar -C data/library/packs`. This works for a pack and for a bundle.

{bundle_section}## Packs

| File | Size | License | Credit | SHA-256 |
|---|---|---|---|---|
{rows}

## Licenses

Each pack keeps the license of its source, so this card uses `license: other`. Every pack contains its own `NOTICE.txt` with the credit, the license name and the full license texts. Read the `NOTICE.txt` of a pack before you reuse it.
""")
