# Datasets

Every source that goes into a pack, with its snapshot, license and processing. The download scripts pin each source to a revision.

Licenses: the tables show the value in each pack's `manifest.json`. A separate license audit is not finished. "License check pending" marks a statement that I did not confirm yet.

All knowledge packs have dense codes (512-bit sign codes of `mixedbread-ai/mxbai-embed-large-v1`, made by `embed.py` on a GPU).

## English Wikipedia (`enwiki`, `enwiki-core`)

- Input: the public Wikimedia XML dump `enwiki-20260901-pages-articles-multistream` (71 files, about 25 GB of bz2) and `enwiki-20260901-page_props.sql.gz` (QIDs), from dumps.wikimedia.org/enwiki/. No account is needed. License: CC BY-SA 4.0.
- Pageviews for the popularity prior: `NeuML/wikipedia-20260401` `pageviews.sqlite` @ `40a94fda39044e78a0fc3d92c07d4f2bef4c4587` (`scripts/fetch-extra-packs.sh pageviews`). The pack stores only a popularity number per article. License of the dataset repo: check pending.
- Processing (`pipeline/commonplace_pipeline/wikipedia.py`): `convert` downloads one dump part at a time, renders the wikitext on 16 cores and deletes the part. `build` merges the shards with QIDs, redirects and pageview popularity. The renderer expands the templates that carry content ({{convert}} with the converted value, dates, {{lang}}, lists, fractions, currency). It turns infobox fields into an "Infobox" passage. On 15 large articles it keeps 0.99 times the words of Wikipedia's own rendering and 87% of the numbers. Most missing numbers are in bibliographies.
- `enwiki`: 6,766,942 articles, 44,598,907 passages, 10,815,154 redirects, QIDs for 99.85% of the articles, 19.2 GB with dense codes (33.5 h on an RTX 5060 Ti at about 370 passages per second). Its manifest says `replaces: enwiki-core`.
- `enwiki-core` (the starter pack, `plan_a.py subset`; the module name is historical): the lead passages of the 2,000,000 most-viewed articles and the full text of the 200,000 most-viewed ones. 6,518,976 passages, 3.54 GB, dense codes for all.
- Monthly freshness: a new dump starts on the 1st of each month. To update, I rebuild the pack from the new dump. A pack has no update layers. `embed.py --reuse <last month's work dir>` copies the codes of passages whose text hash did not change. I have not measured the share of reused passages yet.

## Simple English Wikipedia (`simplewiki`, development pack)

- Input: [`HuggingFaceFW/finewiki`](https://huggingface.co/datasets/HuggingFaceFW/finewiki) `data/simplewiki/000_00000.parquet` @ `8bd13e72e6a002407649b3e898535f42ceb1aeb9` (265,299 pages, modified up to 2025-08). CC BY-SA 4.0.
- `wikimedia/structured-wikipedia` has no Simple English edition (only `enwiki` and `frwiki`), so the development pack uses finewiki.
- Chunking (`chunk.py`): walk the Markdown headings and skip "See also", "References", "Related pages", "Other websites" and similar sections. Pack paragraphs into passages of 80 to 220 words. Linearize tables up to 40 rows. Add one infobox passage per article. Drop disambiguation and "List of" pages. Result: 262,364 articles, 718,387 passages.
- Popularity: finewiki has no pageviews, so the pack uses the HTML size in KB.
- The pack has keyword search only. It is for the emulator and the E2E script. The release does not include it.

## Wikidata (`wikidata-facts`)

- Input: the `permutans/wikidata-claims`, `-links`, `-labels`, `-aliases` and `-claims_labels` tables on Hugging Face. They come from the **Wikidata dump of 2026-05-07** (about 20 GB). `scripts/fetch-wikidata.sh` records the revisions in `data/raw/wikidata/REVISIONS`. Wikidata license: CC0 1.0. License of the `permutans` tables: check pending.
- Processing (`pipeline/commonplace_pipeline/wikidata.py`): keep the items that have an English Wikipedia sitelink and the 230 properties in `pipeline/configs/wikidata-props.tsv` (short labels, display order). Drop deprecated statements. Keep units and point-in-time qualifiers. The sitelinks upload repeats rows across chunk files, so stage 2 removes duplicates.
- Result (all 7,449 claim chunks): 7,303,774 entities, 42,484,096 facts, 3.15 GB. The step also writes `title_qid.parquet`, which gives the English Wikipedia packs their QIDs.
- The app shows the facts that match the question first (for example, "How tall..." shows elevation). It shows the other facts in display order.

## Breadth packs

`scripts/fetch-extra-packs.sh` pins the sources. `pipeline/README.md` has the build commands. A pack without dense codes is keyword-only: the retriever then keeps its top 5 keyword hits in the rerank pool. No pack in this table is keyword-only.

Sizes come from the manifests (`size_bytes`, dense codes included).

| Pack | Source | Snapshot | License (manifest) | Articles / passages | Size |
|---|---|---|---|---|---|
| `wikivoyage-en` | Wikimedia dump `enwikivoyage-20260901` (XML, converted from wikitext) | 2026-09-01 | CC BY-SA 4.0 | 32,378 / 526,315 | 208 MB |
| `stackexchange` | `common-pile/stackexchange` @ `5ec3aa2`, 52 sites | 2024-12-31 | CC BY-SA 2.5/3.0/4.0 per post; check pending | 1,231,965 / 4,284,890 | 2.38 GB |
| `openstax` | `HuggingFaceTB/openstax_paragraphs` | 2024-01-29 | CC BY 4.0 | 4,931 / 16,409 | 8 MB |
| `arxiv-abs` | `librarian-bots/arxiv-metadata-snapshot` @ `1901619`, titles and abstracts | 2026-09-25 | CC0 1.0 (arXiv metadata) | 3,182,867 / 3,314,444 | 3.34 GB |
| `medlineplus` | MedlinePlus health topics XML (NLM) | 2026-09-26 | Public domain (U.S. NLM) | 1,014 / 3,285 | 2 MB |
| `wikibooks-en` | Kiwix ZIM `wikibooks_en_all_nopic_2026-04` | 2026-04 | CC BY-SA 4.0 | 83,521 / 1,010,601 | 432 MB |
| `wikiquote-en` | Kiwix ZIM `wikiquote_en_all_nopic_2026-07` | 2026-07 | CC BY-SA 4.0 | 67,668 / 831,284 | 369 MB |
| `wikiversity-en` | Kiwix ZIM `wikiversity_en_all_nopic_2026-08` | 2026-08 | CC BY-SA 4.0 | 42,937 / 449,719 | 183 MB |
| `wikem-en` | Kiwix ZIM `wikem_en_all_nopic_2026-07`, English pages only | 2026-07 | CC BY-SA 3.0; check pending | 4,236 / 41,867 | 12 MB |
| `archwiki-en` | Kiwix ZIM `archlinux_en_all_maxi_2026-07`, English pages only | 2026-07 | GFDL 1.3 or later | 2,168 / 34,161 | 12 MB |
| `devdocs-en` | Kiwix ZIMs `devdocs_en_*`: bash, C, C++, CSS, Docker, Git, Go, HTML, JavaScript, Kotlin, Nix, Node, NumPy, pandas, PostgreSQL, Python, React, Rust, SQLite, TypeScript | 2026-04 to 2026-08 | Per docset (devdocs.io/about); check pending | 19,321 / 153,517 | 49 MB |
| `cdc-travel` | CDC Travelers' Health destination pages (`cdc_travel.py fetch`, 20 s apart as robots.txt asks) | 2026-09-30 | Public domain (U.S. government work) | 244 / 13,503 | 2 MB |
| `textbooks-en` | `common-pile/pressbooks_filtered` @ `1a1d3b5` and `libretexts_filtered` @ `70388bc`. Front and back matter dropped. | 2025-03 | CC BY, CC BY-SA, public domain or GFDL per chapter; check pending | 93,008 / 863,744 | 524 MB |
| `wiktionary-en` | kaikki.org English Wiktionary extract of 2026-09-28. Lemmas only (no inflected forms, alternative spellings, misspellings or proper names). Articles are not linkable. | 2026-09-28 | CC BY-SA 4.0 and GFDL | 614,411 / 621,042 | 189 MB |
| `factbook` | CIA World Factbook, final edition, from `factbook/factbook.json` @ `144d697`. One article per country, one section per category. Articles are not linkable. | 2026-02 | Public domain; the JSON mirror is CC0 | 261 / 6,505 | 3 MB |

The common-pile Stack Exchange rows have no vote scores or accepted-answer flags. So the builder keeps threads by the number of distinct authors and drops comments with a heuristic. Practical sites (gardening, pets, money, law and 13 others) need 3 distinct authors, math 8 and the other sites 5. The build uses only the 2024-12 common-pile copy.

The WikEM ZIM of 2026-07 holds 93,417 machine-translated subpages ("Sepsis/ru") that are marked as English. `zim.py --drop-translations` skips them. The ArchWiki ZIM also holds translations, marked with their own `lang` attribute. `zim.py` skips every page whose content language is not English.
