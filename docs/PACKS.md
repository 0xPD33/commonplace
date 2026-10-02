# Packs

A pack is a directory of files that the app reads with `mmap`. The Rust crate `commonplace-core` defines the format (`core/commonplace-core/src/pack/`). `packbuild` writes packs through the same code, so the writer and the reader cannot drift.

There are three pack types: `knowledge`, `wikidata` and `model`.

## Layout (format version 1)

```
<pack_id>/
  manifest.json
  meta.sqlite        articles(id, title, title_norm, qid, popularity, url_title, oneliner,
                              first_passage, n_passages), redirects(from_norm, article_id), sources
  store/             passages: dict.zstd, frames.bin, frames.idx
  cards/             optional fact cards, same frame layout, one record per passage (no release pack has them)
  tantivy/           optional BM25 index (tantivy 0.26.2)
  dense/             optional binary IVF: info.json, centroids.f16, lists.idx, codes.bin, ids.bin
  wikidata.sqlite    wikidata packs only
  <model>.gguf       model packs only
```

### Frame store (`store/`, `cards/`)

- `frames.bin` holds zstd frames. Each frame holds 64 records and uses the shared dictionary `dict.zstd`.
- `frames.idx`: magic `CPFI`, u32 version, u32 records per frame, u64 record count, u64 frame count, then `frames + 1` u64 byte offsets.
- A decompressed frame is: u32 `n`, `n + 1` u32 offsets, record bytes.
- A passage record is: u32 article id, u16 ordinal, u16 section-path length, section path, text.
- A card record is one line per fact: `fact text \t passage ids (comma separated)`.

To read passage `p`, the app decompresses frame `p / 64` only.
Passages of one article have consecutive ids (`first_passage .. first_passage + n_passages`).

### Dense index (`dense/`)

- Codes are the first 512 dimensions of an mxbai embedding, sign-binarized, packed like `numpy.packbits` (dimension `d` is bit `0x80 >> (d % 8)` of byte `d / 8`).
- `centroids.f16`: `n_lists × 512` half floats. `lists.idx`: `n_lists + 1` u64 code offsets.
- `codes.bin` (64 bytes per code) and `ids.bin` (u32 passage id per code) are grouped by list.
- Search: the float query picks the `nprobe` best lists, a Hamming scan keeps 1,000 codes, and float·(±1) rescoring keeps 200.
- The dense index can cover fewer passages than the store (`ids.u32` lists the covered ones).

### Manifest

`manifest.json` lists every file with its size and SHA-256, the snapshot date, the license and attribution, the counts, and the `embedder` block.
Every release pack holds a `NOTICE.txt` (credit, license names, disclaimers and license texts). `textbooks-en` and `stackexchange` also hold `CREDITS.txt` or `CREDITS.txt.zst`. `packbuild notice` adds these files and `build`, `model` and `wikidata` take `--notice` and `--credits`. The files are listed in the manifest, so verify and import check them like all other files.
The app refuses a knowledge pack whose `embedder` differs from the installed packs.
`replaces` names packs that this pack supersedes (`enwiki` replaces `enwiki-core`).

### User documents

The app builds a knowledge pack on the device from a document that the user adds ("My documents"): `CommonplaceEngine.add_document(title, pages, listener)`, or `commonplace add-doc <file.txt>` on the desktop. The code is `core/commonplace-core/src/userdoc.rs`. It writes through the same `pack` writers as `packbuild`.

- The core receives the text of each page. Android extracts it from the PDF.
- The pack is `doc-<10 hex of SHA-256 over title and pages>`, with `user_document: true` in the manifest and the license "user document". The library list, verify and remove treat it like any pack.
- The document is one article. Each passage has the section path `p. <page>`, and no passage spans two pages. The chunker follows `chunk.py` (80 to 220 words per passage).
- The dense index has one IVF list, because a document has at most 5,000 passages. Its codes come from the leaf-mt encoder without the query prompt. leaf-mt shares the mxbai vector space, so the manifest records `embedder.doc = MongoDB/mdbr-leaf-mt`. Each pack is searched on its own and fused by rank, so the embedder check skips packs with `user_document`.
- The build runs in `packs/.building-<id>/` and moves into place when it is complete. A document without text, or with more than 5,000 passages, fails with a message that the UI can show.

## Distribution

`packbuild split` writes one plain tar stream of `<pack_id>/` in parts of at most 2 GB, plus `<pack_id>.pack.json` with the SHA-256 of each part.
The app imports the parts through the Android file picker (SAF). It extracts the stream directly, hashes each part and each file as it reads, checks everything against `pack.json` and `manifest.json`, and only then moves the pack into place. After a verified import, it offers to delete the downloaded files.
The app also enforces the 50 GB total footprint before an import starts.

Each GitHub release asset must stay below 2 GiB, so `scripts/release.sh packs` splits at 2,000,000,000 bytes. Packs go in their own release tag, `packs-<snapshot>` (for example `packs-2026-09`), separate from the app releases.
`scripts/release.sh packs <tag> [pack_id...]` verifies and splits the packs, then writes `SHA256SUMS` and `catalog.json` (title, description, type, sizes, license, `replaces`, `recommended`, and for each file its name, size, SHA-256 and download URL). It copies `catalog.json` to `android/app/src/main/assets/catalog.json`. That file is committed, so the APK build is reproducible: commit the new copy after each packs release. The app has no INTERNET permission, so the catalog only gives the user browser links. `scripts/release.sh upload <tag>` creates the pre-release and uploads the files.
The release assets of the packs live under the GitHub release tag `packs-2026-09`. The app releases have their own tags. A user downloads every part and the `pack.json` file of a pack with a browser, then imports them with the file picker (INSTALL.md). On a desktop, `cat <pack_id>.tar.part* | tar -x -C data/library/packs` installs a pack.

## Build a pack

Run all commands from the repository root inside `nix develop` (or with the tools from `docs/INSTALL.md`).

```sh
cd core && cargo build --release -p packbuild && cd ..
PB=core/target/release/packbuild

# Simple English Wikipedia (dev pack, sparse only): chunk, then build.
uv run --project pipeline python -m commonplace_pipeline.chunk --input data/raw/finewiki-simplewiki.parquet --out data/work/simplewiki
$PB build --input data/work/simplewiki --out data/library/packs/simplewiki --pack-id simplewiki \
  --title "Simple English Wikipedia" --snapshot 2025-08 --attribution "Wikipedia contributors; HuggingFaceFW/finewiki"

# English Wikipedia from the monthly dump (pipeline/README.md has the full monthly steps), then the starter subset.
uv run --project pipeline python -m commonplace_pipeline.wikipedia convert --date 20260901 --out data/work/enwiki-20260901
uv run --project pipeline python -m commonplace_pipeline.wikipedia build --work data/work/enwiki-20260901 --pageviews data/raw/pageviews.sqlite
uv run --project pipeline --extra gpu python -m commonplace_pipeline.embed --work data/work/enwiki-20260901
$PB build --input data/work/enwiki-20260901 --out data/library/packs/enwiki --pack-id enwiki --title "English Wikipedia" \
  --snapshot 2026-09-01 --replaces enwiki-core --attribution "Wikipedia contributors; Wikimedia dump enwiki-20260901"
uv run --project pipeline python -m commonplace_pipeline.plan_a subset --src data/work/enwiki-20260901 --out data/work/enwiki-core
$PB build --input data/work/enwiki-core --out data/library/packs/enwiki-core --pack-id enwiki-core \
  --title "English Wikipedia (starter)" --snapshot 2026-09-01 --attribution "Wikipedia contributors; Wikimedia dump enwiki-20260901"

# A model pack.
$PB model --gguf data/models/llm/Ling-3.0-tiny-Q4_0.gguf --out data/library/packs/ling3-tiny --pack-id ling3-tiny \
  --title "Ling 3.0 tiny (Q4_0)" --role llm-fast --hf-repo bartowski/Ling-3.0-tiny-GGUF \
  --revision ea072726af0d2e8ba325b2f90fc0efa762105a91 --license MIT --link

# Notices: copy the notice into a built pack and fix its license or attribution (sources: pipeline/notices/).
$PB notice --pack data/library/packs/wikem-en --notice pipeline/notices/wikem-en.txt --license "CC BY-SA 4.0"

# Dense codes on the GPU for a built keyword-only pack, then swap them in (no tantivy rebuild).
uv run --project pipeline --extra gpu python -m commonplace_pipeline.embed --work data/work/wikivoyage-en
$PB dense --input data/work/wikivoyage-en --pack data/library/packs/wikivoyage-en

# New QIDs or redirects (plan_a.py enrich --title-qid) for a built pack: rewrite meta.sqlite only.
$PB meta --input data/work/<id> --pack data/library/packs/<id>

# Parts for download.
$PB split --pack data/library/packs/enwiki-core --out dist/
$PB verify --pack data/library/packs/enwiki-core
```

### Pipeline input schema (Parquet)

| File | Columns |
|---|---|
| `articles.parquet` | `article_id` u32 (dense from 0), `title`, `qid` (nullable), `popularity` u64, `url_title`, `oneliner`, `first_passage` u32, `n_passages` u32 |
| `passages.parquet` | `passage_id` u32 (dense from 0, grouped by article), `article_id` u32, `ordinal` u16, `section_path`, `text` |
| `redirects.parquet` (optional) | `from_title`, `article_id` u32 |
| `questions.parquet` (optional, unused) | `passage_id` u32, `questions` (joined with newlines) |
| `cards.parquet` (optional, unused) | `passage_id` u32, `fact`, `source_ids` ("12,13") |
| `dense/` (optional) | `codes.u8` (n × 64), `assign.u32`, `centroids.f32`, `info.json` (`dims`), optional `ids.u32` |

The text that `embed.py` embeds for a passage is `"{title} > {section_path}\n{text}"` (`"{title}\n{text}"` when there is no section).
