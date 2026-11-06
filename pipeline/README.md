# Commonplace pipeline

Python steps that make the Parquet inputs for `packbuild`. Run every command from the repo root:

```sh
nix develop -c uv run --project pipeline python -m commonplace_pipeline.<module> ...
```

## Steps

**Wikidata facts:**

```sh
scripts/fetch-wikidata.sh                                    # ~20 GB into data/raw/wikidata, skips files on disk
python -m commonplace_pipeline.wikidata --out data/work/wikidata   # ~2 min on 32 threads
cd core && ./target/release/packbuild wikidata --input ../data/work/wikidata \
  --out ../data/library/packs/wikidata-facts --snapshot 2026-05-07
```

`configs/wikidata-props.tsv` lists the kept properties and their labels, and line order sets the display priority. After you add a property, delete `data/work/wikidata/claims-flat/`. The step also writes `title_qid.parquet` (enwiki title → QID).

## Breadth packs

`wikivoyage-en`, `stackexchange` (51 sites), `openstax`, `arxiv-abs`, `medlineplus`, `cdc-travel`, `textbooks-en`, `wiktionary-en`, `factbook` and the Kiwix packs (`wikibooks-en`, `wikiquote-en`, `wikiversity-en`, `wikem-en`, `archwiki-en`, `devdocs-en`).
`scripts/fetch-extra-packs.sh` downloads the pinned sources into `data/raw/`. The pins are at the top of the script.

```sh
scripts/fetch-extra-packs.sh                      # or name sources: wikivoyage stackexchange openstax arxiv medlineplus textbooks wiktionary zim
python -m commonplace_pipeline.wikivoyage --dump data/raw/wikivoyage/enwikivoyage-20260901-pages-articles.xml.bz2 \
  --page-props data/raw/wikivoyage/enwikivoyage-20260901-page_props.sql.gz --out data/work/wikivoyage-en
python -m commonplace_pipeline.stackexchange --input data/raw/stackexchange --out data/work/stackexchange
python -m commonplace_pipeline.openstax --input data/raw/openstax/openstax_books.jsonl --out data/work/openstax
python -m commonplace_pipeline.arxiv --input data/raw/arxiv --out data/work/arxiv-abs
python -m commonplace_pipeline.medlineplus --input data/raw/medlineplus/mplus_topics_2026-09-26.xml --out data/work/medlineplus
python -m commonplace_pipeline.zim --drop-alt-medicine --input data/raw/zim/wikibooks_en_all_nopic_2026-04.zim --out data/work/wikibooks-en
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

Every released pack needs its notice: pass `--notice pipeline/notices/<id>.txt` to `packbuild build`. `textbooks-en` and `stackexchange` also take `--credits` with the file that `python -m commonplace_pipeline.credits` writes. The table below is the manifest data.

| Pack | Snapshot | License | Attribution |
|---|---|---|---|
| wikivoyage-en | 2026-09-01 | CC BY-SA 4.0 | Wikivoyage contributors; Wikimedia dump enwikivoyage-20260901 |
| stackexchange | 2024-12-31 | CC BY-SA 2.5/3.0/4.0 (per post) | Stack Exchange, Inc. and the contributors of each site (thread URL in each article; authors in CREDITS.txt.zst); common-pile/stackexchange@5ec3aa2 |
| openstax | 2024-01-29 | CC BY 4.0 (text of 2024-01-29; CC BY-NC-SA books left out) | OpenStax, Rice University (openstax.org); HuggingFaceTB/openstax_paragraphs |
| arxiv-abs | 2026-09-25 | CC0 1.0 (arXiv metadata) | arXiv.org metadata via librarian-bots/arxiv-metadata-snapshot@1901619 |
| medlineplus | 2026-09-26 | Public domain (U.S. NLM) | Courtesy of MedlinePlus from the National Library of Medicine |
| wikibooks-en | 2026-04 | CC BY-SA 4.0 and GFDL | Wikibooks contributors; Kiwix ZIM wikibooks_en_all_nopic_2026-04 |
| wikiquote-en | 2026-07 | CC BY-SA 4.0 (editors' selection; the quotations remain their authors' works) | Wikiquote contributors; Kiwix ZIM wikiquote_en_all_nopic_2026-07 |
| wikiversity-en | 2026-08 | CC BY-SA 4.0 and GFDL | Wikiversity contributors; Kiwix ZIM wikiversity_en_all_nopic_2026-08 |
| wikem-en | 2026-07 | CC BY-SA 4.0 | WikEM contributors (wikem.org); Kiwix ZIM wikem_en_all_nopic_2026-07 |
| archwiki-en | 2026-07 | GFDL 1.3 or later | ArchWiki contributors (wiki.archlinux.org); Kiwix ZIM archlinux_en_all_maxi_2026-07 |
| devdocs-en | 2026-04 to 2026-08 | Per docset (see NOTICE.txt and docs/DATASETS.md) | DevDocs (devdocs.io) and the authors of each documentation set; Kiwix ZIMs devdocs_en_* |
| cdc-travel | 2026-09-30 | Public domain (U.S. government work) | Centers for Disease Control and Prevention, Travelers' Health (wwwnc.cdc.gov/travel). This content is not endorsed by CDC or HHS. |
| textbooks-en | 2025-03 | CC BY, CC BY-SA, CC0, public domain or GFDL (per book; see CREDITS.txt) | Authors of each book (CREDITS.txt and the chapter URL); common-pile pressbooks_filtered@1a1d3b5 and libretexts_filtered@70388bc |
| wiktionary-en | 2026-09-28 | CC BY-SA 4.0 and GFDL | Wiktionary contributors; kaikki.org English extract (Tatu Ylonen, wiktextract) of 2026-09-28 |
| factbook | 2026-02 | Public domain (U.S. government work); JSON mirror CC0 | Central Intelligence Agency, The World Factbook (public domain); factbook/factbook.json@144d697 (CC0) |

`zim` reads Kiwix ZIM files with `python-libzim` and skips pages in other languages. `--prefix` puts the DevDocs docset name before each title. `--drop-alt-medicine` drops the Wikibooks alternative-medicine books, whose home remedies reached first-aid answers.

## Dense codes on the GPU

`embed` embeds a work directory with mxbai-embed-large-v1 and writes `dense/` for `packbuild`: 512-bit sign codes, an IVF with about 1,500 codes per list, and the list assignments. After a stop, the same command skips the finished shards. The 5060 Ti embeds about 330–640 passages per second.

```sh
nix develop -c uv run --project pipeline --extra gpu python -m commonplace_pipeline.embed --work data/work/<id>
```

To make the starter pack, run `plan_a.py subset` on the embedded work directory. The subset copies the codes.

## Reranker distillation

`rerank` fine-tunes the phone reranker (Ettin-17m) on our own retrieval pools, with a large cross-encoder as the teacher.
`commonplace pool` writes the pools: one JSON line per query with the fused hits, in the reranker's input format.

```sh
core/target/release/commonplace --library data/library --no-llm --no-rerank pool < queries.jsonl > pool.jsonl
python -m commonplace_pipeline.rerank score --pools pool.jsonl --model <teacher> --out teacher.jsonl   # resumable
python -m commonplace_pipeline.rerank train --pools pool.jsonl --teacher teacher.jsonl \
  --dev-pools nq-dev-pool.jsonl --dev-answers nq-dev.jsonl --out data/models/<student>
python -m commonplace_pipeline.rerank export --model data/models/<student>           # onnx/ + int8 variants + parity check
python -m commonplace_pipeline.rerank sel8 --src data/models/<student>/onnx/model.onnx --dst data/models/<student>/onnx/model_sel8.onnx
python -m commonplace_pipeline.rerank hitk --pools nq-dev-pool.jsonl --answers nq-dev.jsonl --scores teacher.jsonl
```

`train` picks the checkpoint by hit@1 on the first 1,000 NQ-open validation queries, so report results with `--offset 1000`. `sel8` writes the phone's file: dynamic int8, except layer 0's MLP output MatMul, which stays fp32. Check a new file against its fp32 source on the labeled pools and NQ before you ship it.

The app's reranker came from this step with `cross-encoder/ettin-reranker-400m-v1` as the teacher, on 23,012 pools. Its run used a variant of `train` that is not in this repository. The model card has the settings. To test a reranker change without new judge costs, score it against the 161 pools that Claude Sonnet graded (nDCG@8, see docs/BENCHMARKS.md).

## Learned fusion, sentence head and output rows

Each module's docstring has the full commands and results.

- `fusion` trains the scorer that orders search candidates before the rerank pool (`core/commonplace-core/src/fusion_model.json`). Its training questions contain no Stack Exchange text, because those dumps forbid LLM training.
- `sentsel` trains the head that scores sentence pieces of a passage (`sentence_head.json`). Claude grades the pieces.
- `head_rows` writes `core/commonplace-llm/src/ling_head_rows.bin`, the rows of Ling's output head that the app computes. A new list can change answers: run the release gate after you replace the file. A rebuild does not give the same bytes, because the answers in `eval/runs` change over time.

## Classifier heads and property vectors

`heads` trains a softmax layer on the leaf-mt question embedding and writes `core/commonplace-core/src/<name>_head.json`. The examples are in `configs/<name>-examples.jsonl` and `configs/generated/<name>-*.jsonl`. `--eval` only reports.

```sh
uv run --project pipeline python -m commonplace_pipeline.heads turn-kind --near 1 --l2 1e-4 --merge followup=question --allow reformat=6 \
  --eval eval/conversation.jsonl --map followup=question,compare_followup=question,topic_switch=question,expand=expand,reformat=reformat,chat=chat
uv run --project pipeline python -m commonplace_pipeline.heads follow-up --examples turn-kind --near 1 --l2 1e-4 \
  --merge expand=followup --only question,followup --strict none \
  --eval eval/conversation.jsonl --map followup=followup,compare_followup=followup,expand=followup,topic_switch=question
uv run --project pipeline python -m commonplace_pipeline.heads intent --strict calc \
  --eval eval/queries.jsonl --map factual=lookup,explanation=explain,comparison=compare,numeric=calc
```

- `--strict` names the labels whose mistakes are costly. Cross-validation sets a bar for each strict label (`min_probs`), so that no held-out example of another label reaches it. `--allow label=k` leaves out the k highest mistakes for that label.

`prop_vectors` writes `core/commonplace-core/src/wikidata_props.json`, one vector per kept property. Run it again after you change the property list or the query encoder:

```sh
uv run --project pipeline python -m commonplace_pipeline.prop_vectors   # needs data/raw/wikidata and data/work/wikidata
```

## Wikipedia, monthly (public XML dumps, no account)

`wikipedia.py` turns a monthly dump from dumps.wikimedia.org/enwiki/ into packbuild input. The build also needs the pageviews file: `scripts/fetch-extra-packs.sh pageviews`. `convert` deletes each dump part after rendering, so the full ~25 GB never sits on disk.

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
