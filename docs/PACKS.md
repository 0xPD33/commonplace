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
  <model>.gguf       model packs only (a voice pack holds other files instead: role `stt` the Moonshine files, role `tts`
                     the Phonon folder with its default voice; `model.file` names the main one)
```

### Frame store (`store/`, `cards/`)

- `frames.bin` holds zstd frames. Each frame holds 64 records and uses the shared dictionary `dict.zstd`.
- `frames.idx`: magic `CPFI`, u32 version, u32 records per frame, u64 record count, u64 frame count, then `frames + 1` u64 byte offsets.
- A decompressed frame is: u32 `n`, `n + 1` u32 offsets, record bytes.
- A passage record is: u32 article id, u16 ordinal, u16 section-path length, section path, text.
- A card record is one line per fact: `fact text \t passage ids (comma separated)`.

To read passage `p`, the app decompresses frame `p / 64` only.
Passages of one article have consecutive ids (`first_passage .. first_passage + n_passages`).
The app removes wiki reference marks (`[1]`, `[2][3]`, `[citation needed]`, `[a]` after punctuation) from the text when it reads a passage, so the packs keep the text as built. It skips `devdocs-en` and user documents (`Pack::passage`, `text::strip_ref_marks`).

### Dense index (`dense/`)

- Codes are the first 512 dimensions of an mxbai embedding, sign-binarized, packed like `numpy.packbits` (dimension `d` is bit `0x80 >> (d % 8)` of byte `d / 8`).
- `centroids.f16`: `n_lists × 512` half floats. `lists.idx`: `n_lists + 1` u64 code offsets.
- `codes.bin` (64 bytes per code) and `ids.bin` (u32 passage id per code) are grouped by list.
- Search: the float query picks the `nprobe` best lists, a Hamming scan keeps 1,000 codes, and float·(±1) rescoring keeps 200.
- The dense index can cover fewer passages than the store (`ids.u32` lists the covered ones).

### Manifest

`manifest.json` lists every file with its size and SHA-256, the snapshot date, the license and attribution, the counts, and the `embedder` block. A model pack's `model` block names the file, its role and, optionally, a `profile` with the model's defaults in the app (`commonplace_core::profiles`: `default`, `gemma4`, `gemma4-npu`; `packbuild model --profile`). Without one, the file name picks it.
Every release pack holds a `NOTICE.txt` (credit, license names, disclaimers and license texts). `textbooks-en` and `stackexchange` also hold `CREDITS.txt` or `CREDITS.txt.zst`. `packbuild notice` adds these files and `build`, `model` and `wikidata` take `--notice` and `--credits`. The files are listed in the manifest, so verify and import check them like all other files.
The app refuses a knowledge pack whose `embedder` differs from the installed packs.
`replaces` names packs that this pack supersedes (`enwiki` replaces `enwiki-core`).

### User documents

The app builds a knowledge pack on the device from a document that the user adds: `CommonplaceEngine.add_document(title, pages, listener)`, or `commonplace add-doc <file.txt>` on the desktop (`core/commonplace-core/src/userdoc.rs`). "Ask this document" limits the questions that follow to that pack (`AskRequest.packs`; `commonplace ask|retrieve --pack <id>` on the desktop).

- The core receives the text of each page. Android extracts it from the PDF.
- The pack is `doc-<10 hex of SHA-256 over title and pages>`, with `user_document: true` in the manifest.
- The document is one article. Each passage has the section path `p. <page>`, and no passage spans two pages. The chunker follows `chunk.py` (80 to 220 words per passage).
- The dense index has one IVF list, because a document has at most 5,000 passages. Its codes come from the leaf-mt encoder without the query prompt, which shares the mxbai vector space (`embedder.doc = MongoDB/mdbr-leaf-mt`).
- A document without text, or with more than 5,000 passages, fails with a message that the UI can show.

## Distribution

### Files

- **Single pack.** `packbuild split --single` writes one plain tar stream of `<pack_id>/` as `<pack_id>.tar`. Without `--single`, it writes parts of at most `--part-size` bytes (2 GB by default) plus `<pack_id>.pack.json` with the SHA-256 of each part. The app still imports such multi-part packs.
- **Bundle.** `packbuild bundle --pack <dir>... --out <file>.tar` writes one tar stream with several top-level `<pack_id>/` directories. A bundle is a container, not a pack format, and it has no manifest of its own. The `BUNDLES` list at the top of `scripts/release.sh` defines the bundles: the two starter sets and the Reference shelf (13 packs). Large packs (`enwiki`, `arxiv-abs`, `stackexchange`) stay single. A pack inside a bundle has no file of its own, except `minicpm5-2b`.
- **Desktop install.** `tar -xf <file>.tar -C data/library/packs` installs a pack or a bundle.

### Import

The app imports a file through the Android file picker. It extracts the stream, hashes the file and each file inside it as it reads, checks everything against `manifest.json` (and the catalog's SHA-256, for a catalog pack), and only then moves the pack into place. It enforces the 50 GB limit before the import starts, using the size of the whole file for a bundle. After a verified import, it offers to delete the download.

In a bundle, the importer handles each pack like a single pack:

- Nothing moves into place until the whole file has passed its hash, so a failed import installs nothing and removes its partial files.
- A file outside the catalog cannot replace an installed pack, must hold only the pack in its file name, and shows "not from the app's catalog" in the Library.
- The app names each pack while it installs (`ImportListener.on_pack`), and `import_pack` returns the ids of all installed packs.

### Release

The catalog packs live in the Hugging Face dataset. **A file that a catalog lists never changes**, because older apps check it against their own SHA-256. So a rebuilt pack or bundle goes in a new folder `packs-<date>` (for example `packs-2026-11`), and unchanged files stay in their old folder.

- `HF_REPO=<namespace>/<name> scripts/release.sh packs <tag> [pack_id...]` verifies the packs and writes the `.tar` files, `SHA256SUMS`, `catalog.json` and `README.md` (the dataset card) to `dist/<tag>`. It also copies `catalog.json` to `android/app/src/main/assets/catalog.json`: commit that copy, because the APK build reads it.
- `HF_REPO=<namespace>/<name> scripts/release.sh upload <tag>` uploads the files. It needs a write token (`HF_TOKEN`), and it is the only mode that writes to Hugging Face.

### Catalog

For each pack, `catalog.json` has the title, description, type, sizes, license, `build_date`, `replaces`, `recommended`, and the name, size, SHA-256 and download URL of its file. The URL is `https://huggingface.co/datasets/0xPD33/commonplace-packs/resolve/main/<tag>/<file>?download=true`.

The top-level list `bundles` has `bundle_id`, `title`, `description`, `pack_ids`, `members` (`pack_id`, `title`, `build_date`), `download_bytes`, `installed_bytes`, `files` and `recommended`. Only the two starter bundles are recommended, and the app shows the one that suits the phone's RAM.

The app has no `INTERNET` permission, so the catalog only gives the user browser links. The app compares `build_date` with the installed pack's manifest and lists the download under Updates when the catalog's build is newer. Entries from catalogs before 0.4.0 have no `build_date` and never show as updates.

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
# The voice pack: every file of a directory, role stt (only the Kotlin app loads it).
$PB model --dir data/models/stt/moonshine-medium-streaming-en --file encoder.ort --out data/library/packs/voice-en --pack-id voice-en \
  --title "Voice input (Moonshine Medium, English)" --role stt --hf-repo download.moonshine.ai/model/medium-streaming-en \
  --revision quantized_26_08_21 --license MIT --notice pipeline/notices/voice-en.txt \
  --attribution "Moonshine Medium Streaming EN by Moonshine AI (UsefulSensors); download.moonshine.ai"
# The read-aloud voice: the same, role tts (scripts/fetch-models.sh tts gets the files).
$PB model --dir data/models/tts/phonon-31m-en --file model.q8.gguf --out data/library/packs/tts-en --pack-id tts-en \
  --title "Read aloud voice (Phonon, English)" --role tts --hf-repo gradium/phonon-31m-en \
  --revision 92741c5b07d085ff840e26fea8ce43110b87852f --license Apache-2.0 --notice pipeline/notices/tts-en.txt \
  --attribution "Phonon 31m English by Gradium (builds on Pocket TTS by Kyutai); gradium/phonon-31m-en"

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
