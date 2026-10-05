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

The app builds a knowledge pack on the device from a document that the user adds ("My documents"): `CommonplaceEngine.add_document(title, pages, listener)`, or `commonplace add-doc <file.txt>` on the desktop. The code is `core/commonplace-core/src/userdoc.rs`. It writes through the same `pack` writers as `packbuild`. "Ask this document" in Library limits the questions that follow to that pack (`AskInput.packs` in the FFI, `AskRequest.packs` in the core; `commonplace ask|retrieve --pack <id>` on the desktop). Retrieval, entity linking and Wikidata facts skip every pack outside the list. An empty list means every enabled pack.

- The core receives the text of each page. Android extracts it from the PDF.
- The pack is `doc-<10 hex of SHA-256 over title and pages>`, with `user_document: true` in the manifest and the license "user document". The library list, verify and remove treat it like any pack.
- The document is one article. Each passage has the section path `p. <page>`, and no passage spans two pages. The chunker follows `chunk.py` (80 to 220 words per passage).
- The dense index has one IVF list, because a document has at most 5,000 passages. Its codes come from the leaf-mt encoder without the query prompt. leaf-mt shares the mxbai vector space, so the manifest records `embedder.doc = MongoDB/mdbr-leaf-mt`. Each pack is searched on its own and fused by rank, so the embedder check skips packs with `user_document`.
- The build runs in `packs/.building-<id>/` and moves into place when it is complete. A document without text, or with more than 5,000 passages, fails with a message that the UI can show.

## Distribution

`packbuild split --single` writes one plain tar stream of `<pack_id>/` as `<pack_id>.tar`. Without `--single`, it writes parts of at most `--part-size` bytes (2 GB by default) plus `<pack_id>.pack.json` with the SHA-256 of each part. The app still imports such multi-part packs, for example from a third party.
The app imports the file through the Android file picker (SAF). It extracts the stream directly, hashes the file and each file inside it as it reads, checks everything against `manifest.json` (and against the SHA-256 of the catalog, for a catalog pack), and only then moves the pack into place. After a verified import, it offers to delete the downloaded files.
The app also enforces the 50 GB total footprint before an import starts. For a bundle, it uses the size of the whole file.

A bundle is a distribution container, not a new pack format: one tar stream with several top-level `<pack_id>/` directories, one after the other. Pack format version 1 does not change, and a bundle has no manifest of its own. `packbuild bundle --pack <dir>... --out <file>.tar` writes the packs in the given order. The importer handles each pack like a single pack:
- It checks the pack against its own `manifest.json`, runs the compatibility check for that manifest, and moves the pack into place when the next pack starts. The last pack waits until the whole file is read.
- If a pack replaces an installed pack with the same id, the import replaces it, as for a single pack.
- If an error stops the import, the packs that were complete before it stay installed. The error names the failed pack.
- The SHA-256 of the whole file (from the catalog) is checked while the file is read. A mismatch is reported before the last pack goes into place.
- The app tells the user which pack it installs (`ImportListener.on_pack`), and `import_pack` returns the ids of all installed packs.

The catalog packs live in a Hugging Face dataset repo, one `.tar` file per pack. Hugging Face has no 2 GiB limit, so a user downloads one file per pack. Bundles reduce this further: the Starter set (`enwiki-core`, `ling3-tiny` and `wikidata-facts`, 11.3 GB) is one file, so getting started is one download. The Reference shelf (13 breadth packs, about 2 GB) is one file as well. Large packs (`enwiki`, `arxiv-abs`, `stackexchange`, `lfm25-1.2b`) stay single. A pack inside a bundle has no `.tar` file of its own. The packs of a release go in the folder `packs-<snapshot>` of the repo (for example `packs-2026-09`). The app releases have their own tags on GitHub.
`HF_REPO=<namespace>/<name> scripts/release.sh packs <tag> [pack_id...]` verifies the packs and writes the `.tar` files, `SHA256SUMS`, `catalog.json` and `README.md` (the dataset card) to `dist/<tag>`. `HF_REPO` has no default.
The `BUNDLES` list at the top of `scripts/release.sh` defines the bundles: one line per bundle with the id, title, description and pack ids in tar order. The script writes the bundle `commonplace-<bundle_id>.tar` when all its packs are in the `pack_id` list of the call. A bundle that is already in the output directory stays in the catalog. `BUNDLES=...` in the environment replaces the list, for a test.
`catalog.json` has the title, description, type, sizes, license, `replaces` and `recommended` of each pack, and the name, size, SHA-256 and download URL of its file. The URL is `https://huggingface.co/datasets/0xPD33/commonplace-packs/resolve/main/<tag>/<file>?download=true`. The query `download=true` makes the browser save the file.
The top-level list `bundles` has `bundle_id`, `title`, `description`, `pack_ids`, `download_bytes`, `installed_bytes`, `files` (as for a pack) and `recommended` (only the starter bundle). A catalog without `bundles` is still valid.
`README.md` has the license front matter and a table of the bundles, then a table with the file, size, license, credit and SHA-256 of each pack. Each pack also holds its own `NOTICE.txt` with the full license texts.
The script copies `catalog.json` to `android/app/src/main/assets/catalog.json`. That file is committed, so the APK build is reproducible: commit the new copy after each packs release. The app has no INTERNET permission, so the catalog only gives the user browser links.
`HF_REPO=<namespace>/<name> scripts/release.sh upload <tag>` creates the dataset repo if it is missing and uploads the files with the Hugging Face CLI (run through `uvx`). It needs a write token (`HF_TOKEN`). It is the only mode that writes to Hugging Face.
A user downloads the `.tar` file of a bundle or of each pack with a browser, then imports the files with the file picker (INSTALL.md). On a desktop, `tar -xf <file>.tar -C data/library/packs` installs a pack or a bundle, because each pack is a top-level directory of the file.

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

# One file for download (scripts/release.sh packs does this for a whole release).
$PB split --single --pack data/library/packs/enwiki-core --out dist/
$PB verify --pack data/library/packs/enwiki-core
# One file for several packs (a bundle).
$PB bundle --pack data/library/packs/factbook data/library/packs/cdc-travel --out dist/commonplace-test.tar
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
