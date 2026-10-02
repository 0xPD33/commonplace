# Commonplace pipeline

Python steps that make the Parquet inputs for `packbuild`. Run every command from the repo root:

```sh
nix develop -c uv run --project pipeline python -m commonplace_pipeline.<module> ...
```

## How to run

**doc2query** (PLAN §6.3a) and **fact cards** (§6.3b) need an OpenAI-compatible server (vLLM, SGLang or llama-server).
They write shards to `<out-dir>/shards/` at least every 10 minutes. After a stop, run the same command again: it skips the finished passages or sections.
At the end, each command merges the shards into one file. `--merge-only` repeats only the merge.

```sh
# questions.parquet: lead passages of the top 1M articles, then all passages of the top 200k
python -m commonplace_pipeline.doc2query --work data/work/enwiki --base-url http://localhost:8000/v1 --model Qwen/Qwen3.5-2B \
  --lead-articles 1000000 --full-articles 200000 --concurrency 128

# cards.parquet: facts per (article, section) of the top 200k articles
python -m commonplace_pipeline.cards --work data/work/enwiki --base-url http://localhost:8000/v1 --model Qwen/Qwen3.5-9B \
  --articles 200000 --concurrency 64
```

The cards merge runs the hallucination filter again on every fact, so a filter change needs no new generation.
It prints the drop rate and appends it to `<out-dir>/cards.log`. A drop rate above ~10% means that the prompt or the model needs work.
By default, each request sends `chat_template_kwargs.enable_thinking=false`. Use `--extra '{}'` for a server that rejects it.

**Wikidata facts** (§6.5):

```sh
scripts/fetch-wikidata.sh                                    # ~15 GB into data/raw/wikidata, skips files on disk
python -m commonplace_pipeline.wikidata --out data/work/wikidata   # ~2 min on 32 threads
cd core && ./target/release/packbuild wikidata --input ../data/work/wikidata \
  --out ../data/library/packs/wikidata-facts --snapshot 2026-05-07
```

`configs/wikidata-props.tsv` lists the kept properties and their labels. Line order sets the display priority.
Stage 1 keeps its output in `data/work/wikidata/claims-flat/` and skips files it already did. After you add a property, delete that folder.
The step also writes `data/work/wikidata/title_qid.parquet` (enwiki title → QID).

## Breadth packs

`wikivoyage-en`, `stackexchange`, `openstax`, `arxiv-abs`, `medlineplus` (PLAN §6.1, M4) and the Kiwix packs (ADDENDUM §3.3).
`scripts/fetch-extra-packs.sh` downloads the pinned sources into `data/raw/`. The pins are at the top of the script.

```sh
scripts/fetch-extra-packs.sh                      # or name sources: wikivoyage stackexchange openstax arxiv medlineplus textbooks wiktionary zim
python -m commonplace_pipeline.wikivoyage --dump data/raw/wikivoyage/enwikivoyage-20260901-pages-articles.xml.bz2 \
  --page-props data/raw/wikivoyage/enwikivoyage-20260901-page_props.sql.gz --out data/work/wikivoyage-en
python -m commonplace_pipeline.stackexchange --input data/raw/stackexchange --out data/work/stackexchange
python -m commonplace_pipeline.openstax --input data/raw/openstax/openstax_books.jsonl --out data/work/openstax
python -m commonplace_pipeline.arxiv --input data/raw/arxiv --out data/work/arxiv-abs
python -m commonplace_pipeline.medlineplus --input data/raw/medlineplus/mplus_topics_2026-09-26.xml --out data/work/medlineplus
python -m commonplace_pipeline.zim --input data/raw/zim/wikibooks_en_all_nopic_2026-04.zim --out data/work/wikibooks-en
python -m commonplace_pipeline.zim --prefix --input data/raw/zim/devdocs_en_*.zim --out data/work/devdocs-en
python -m commonplace_pipeline.textbooks --input data/raw/common-pile/pressbooks-0000.json.gz \
  data/raw/common-pile/libretexts-0000.json.gz --out data/work/textbooks-en
python -m commonplace_pipeline.wiktionary --input data/raw/kaikki/kaikki.org-dictionary-English.jsonl --out data/work/wiktionary-en
python -m commonplace_pipeline.factbook --input data/raw/factbook/factbook.json-144d697* --out data/work/factbook
python -m commonplace_pipeline.cdc_travel fetch --out data/raw/cdc-travel            # 244 pages, 20 s apart (~80 min)
python -m commonplace_pipeline.cdc_travel build --input data/raw/cdc-travel --out data/work/cdc-travel

core/target/release/packbuild build --input data/work/<id> --out data/library/packs/<id> --pack-id <id> \
  --title "<title>" --snapshot <date> --license "<license>" --attribution "<attribution>"
scripts/check-pack.sh <id> "<question>"           # inside nix develop; writes data/work/<id>/check.txt
```

Without `data/work/<id>/dense/`, `packbuild` builds a keyword-only pack. The next section adds the dense index.

| Pack | Snapshot | License | Attribution |
|---|---|---|---|
| wikivoyage-en | 2026-09-01 | CC BY-SA 4.0 | Wikivoyage contributors; Wikimedia dump enwikivoyage-20260901 |
| stackexchange | 2024-12-31 | CC BY-SA 2.5/3.0/4.0 (per post) | Stack Exchange contributors (links in each thread URL); common-pile/stackexchange@5ec3aa2 |
| openstax | 2024-01-29 | CC BY 4.0 | OpenStax, Rice University (openstax.org); HuggingFaceTB/openstax_paragraphs |
| arxiv-abs | 2026-09-25 | CC0 1.0 (arXiv metadata) | arXiv.org metadata via librarian-bots/arxiv-metadata-snapshot@1901619 |
| medlineplus | 2026-09-26 | Public domain (U.S. NLM) | MedlinePlus, U.S. National Library of Medicine (medlineplus.gov) |
| wikibooks-en | 2026-04 | CC BY-SA 4.0 | Wikibooks contributors; Kiwix ZIM wikibooks_en_all_nopic_2026-04 |
| wikiquote-en | 2026-07 | CC BY-SA 4.0 | Wikiquote contributors; Kiwix ZIM wikiquote_en_all_nopic_2026-07 |
| wikiversity-en | 2026-08 | CC BY-SA 4.0 | Wikiversity contributors; Kiwix ZIM wikiversity_en_all_nopic_2026-08 |
| wikem-en | 2026-07 | CC BY-SA 3.0 | WikEM contributors (wikem.org); Kiwix ZIM wikem_en_all_nopic_2026-07 |
| archwiki-en | 2026-07 | GFDL 1.3 or later | ArchWiki contributors (wiki.archlinux.org); Kiwix ZIM archlinux_en_all_maxi_2026-07 |
| devdocs-en | 2026-04 to 2026-08 | Per docset (listed at devdocs.io/about) | DevDocs (devdocs.io) and the authors of each documentation set; Kiwix ZIMs devdocs_en_* |
| cdc-travel | 2026-09-30 | Public domain (U.S. government work) | Centers for Disease Control and Prevention, Travelers' Health (wwwnc.cdc.gov/travel) |
| textbooks-en | 2025-03 | CC BY, CC BY-SA, public domain or GFDL (per chapter) | Authors of each book (see the chapter URL); common-pile pressbooks_filtered@1a1d3b5 and libretexts_filtered@70388bc |
| wiktionary-en | 2026-09-28 | CC BY-SA 4.0 and GFDL | Wiktionary contributors; kaikki.org English extract (Tatu Ylonen, wiktextract) of 2026-09-28 |
| factbook | 2026-02 | Public domain (U.S. government work); JSON mirror CC0 | Central Intelligence Agency, The World Factbook; factbook/factbook.json@144d697 |

The common-pile Stack Exchange rows have no scores, accepted flags or post boundaries. So `stackexchange` keeps threads by distinct-author count and drops comments by a heuristic (see the module docstring).

`zim` reads Kiwix ZIM files with `python-libzim`. MediaWiki pages keep only `.mw-parser-output`, without references, navboxes and figures, and pages in other languages are skipped (ArchWiki ships its translations in the English ZIM). `--prefix` puts the DevDocs docset name before each title ("Python: itertools").

## Dense codes on the GPU

`embed` embeds a work directory with mxbai-embed-large-v1 (fp16, CLS pooling, no prompt; the text is "title > section" + newline + passage) and writes `dense/` for `packbuild`: 512-bit sign codes, an IVF with about 1,500 codes per list, and the list assignments.
It writes shards of 50,000 passages to `dense/shards/`; after a stop, the same command skips the finished shards. The 5060 Ti embeds about 330–640 passages per second (longer passages are slower).
On 4,000 `enwiki` rows, the codes match the prebuilt Plan A codes (median Hamming distance 0 of 512 bits, max 3).

```sh
nix develop -c uv run --project pipeline --extra gpu python -m commonplace_pipeline.embed --work data/work/<id>
```

For `enwiki-extra-core`, embed `enwiki-extra` and run `plan_a.py subset` again: the subset copies the codes.

## Reranker distillation

`rerank` fine-tunes the phone reranker (Ettin-17m) on our own retrieval pools, with a large cross-encoder as the teacher.
`commonplace pool` writes the pools: one JSON line per query with the fused hits, in the reranker's input format.

```sh
core/target/release/commonplace --library data/library --no-llm --no-rerank pool < queries.jsonl > pool.jsonl
python -m commonplace_pipeline.rerank score --pools pool.jsonl --model <teacher> --out teacher.jsonl   # resumable
python -m commonplace_pipeline.rerank train --pools pool.jsonl --teacher teacher.jsonl \
  --dev-pools nq-dev-pool.jsonl --dev-answers nq-dev.jsonl --out data/models/<student>
python -m commonplace_pipeline.rerank export --model data/models/<student>           # onnx/ + int8 variants + parity check
python -m commonplace_pipeline.rerank hitk --pools nq-dev-pool.jsonl --answers nq-dev.jsonl --scores teacher.jsonl
```

`hitk` counts a query as a hit at k when a normalized answer string occurs in one of the top k passages (the DPR measure). `train` picks the checkpoint by hit@1 on the first 1,000 NQ-open validation queries, so report results with `--offset 1000`.

## Wikipedia, monthly (public XML dumps, no account)

`wikipedia.py` turns a monthly dump from dumps.wikimedia.org/enwiki/ into packbuild input. `convert` downloads one dump part at a time and deletes it after rendering, so the full ~25 GB never sits on disk. A new dump starts on the 1st of each month and is usually complete by the 3rd–5th.

```sh
python -m commonplace_pipeline.wikipedia convert --date 20261001 --out data/work/enwiki-20261001      # ~2.5 h on 16 cores
python -m commonplace_pipeline.wikipedia build --work data/work/enwiki-20261001 --pageviews data/raw/pageviews.sqlite
uv run --project pipeline --extra gpu python -m commonplace_pipeline.embed --work data/work/enwiki-20261001 \
  --reuse data/work/enwiki-20260901                  # embeds only new or changed passages; keeps last month's IVF lists
core/target/release/packbuild build --input data/work/enwiki-20261001 --out data/library/packs/enwiki --pack-id enwiki \
  --title "English Wikipedia" --snapshot 2026-10-01 --license "CC BY-SA 4.0" \
  --attribution "Wikipedia contributors; Wikimedia dump enwiki-20261001"
```

The first dump has nothing to reuse: 44.6M passages took 33.5 h on the 5060 Ti.
