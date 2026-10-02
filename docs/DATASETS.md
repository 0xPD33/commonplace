# Datasets

Every source that goes into a pack, with its snapshot, license and processing. The download scripts pin each source to a commit.

## English Wikipedia, monthly dumps (`enwiki`, `enwiki-core`)

- Input: the public Wikimedia XML dump `enwiki-20260901-pages-articles-multistream` (71 files, ~25 GB bz2) and `enwiki-20260901-page_props.sql.gz` (QIDs), from dumps.wikimedia.org/enwiki/ (no account, a new dump on the 1st of each month). CC BY-SA 4.0.
- Processing (`pipeline/commonplace_pipeline/wikipedia.py`): `convert` downloads one part at a time, renders the wikitext on 16 cores and deletes the part; `build` merges the shards with QIDs, redirects and pageview popularity. The renderer expands the templates that carry content ({{convert}} with the converted value, dates, {{lang}}, lists, fractions, currency) and turns infobox fields into an "Infobox" passage. Against Wikipedia's own rendering on 15 large articles it keeps 0.99× the words and 87% of the numbers (most misses are bibliographies).
- Result: 6,766,942 articles, 44,598,907 passages, 11,299,539 redirects, QIDs for 99.9% of articles; pack 19.2 GB with dense codes (33.5 h on the 5060 Ti at ~370 passages/s). It replaces `enwiki` (2023) and `enwiki-extra`, which together hold 16.2 GB.
- Live since 2026-10-02 as `enwiki` (manifest `replaces: enwiki-core`) and `enwiki-core` (`plan_a.py subset`: leads of the top 2M articles, full text of the top 200k; 6,518,976 passages, 3.54 GB, dense codes for all).
- `dense/hashes.u64` keeps a hash of each embedded text, so next month's `embed.py --reuse data/work/enwiki-20260901` embeds only new or changed passages.

## English Wikipedia, Plan A (2023, replaced)

Replaced on 2026-10-02 by the 2026-09-01 dump above; the 2023 packs, work dirs and raw files were deleted. The notes below explain the old numbers in the eval history.

| Input | Revision | License |
|---|---|---|
| [`mixedbread-ai/wikipedia-data-en-2023-11`](https://huggingface.co/datasets/mixedbread-ai/wikipedia-data-en-2023-11): 41,488,110 passages, 2023-11-01 dump, title + text, no section paths | `a659477aa9be6b5a07d8f2ae3106420d265e70c3` | CC BY-SA 4.0 (Wikipedia text) |
| [`sentence-transformers/quantized-retrieval-data`](https://huggingface.co/datasets/sentence-transformers/quantized-retrieval-data) `wikipedia_ubinary_faiss_50m.index`: mxbai-embed-large-v1 1024-bit sign codes | `205fec4e274dd34c0804ce64a4560f9f604dfe36` | Apache-2.0 repo; codes derive from CC BY-SA text |
| [`NeuML/wikipedia-20260401`](https://huggingface.co/datasets/NeuML/wikipedia-20260401) `pageviews.sqlite`: `pages(title, views)`, 54.6M titles | `40a94fda39044e78a0fc3d92c07d4f2bef4c4587` | Wikimedia pageviews are CC0 |

Processing (`pipeline/commonplace_pipeline/plan_a.py`):

1. Keep the first 512 bits of each code. mxbai is trained for Matryoshka truncation and the binarization is per dimension, so these bits are the 512-dimension binary code.
2. Group passages into articles by the page id in `_id` (`20231101.en_<page>_<chunk>`). Attach pageviews by title and Wikidata QIDs from `title_qid.parquet`.
3. Train a 16,384-list IVF with spherical k-means on 2M sampled codes (as ±1 vectors), then assign every code to its nearest centroid.
4. `plan_a.py enrich` recomputes popularity (pageview titles are lowercase with underscores; 5,838,142 of 5,854,898 articles match), marks 29,344 disambiguation pages as not linkable (they stay searchable), and writes 2,230,816 redirects from Wikidata labels and aliases. Only multi-word aliases and capitalized single words of 4+ letters become redirects, so "northern lights" leads to *Aurora* but "space" does not lead to *Universe*. When two articles claim an alias, the more viewed one wins.

**Alignment finding (2026-09-29).** I re-embedded sample rows with mxbai-embed-large-v1 (CLS pooling, input `"{title}\n{text}"`, no prompt) and compared the sign bits with the prebuilt codes:

- The index holds 40,476,205 codes; the text holds 41,488,110 rows.
- Rows 0 to 35,416,679 (text shards 0–34) match the code with the same index: Hamming distance 0–3 of 1,024 bits.
- From row 35,416,680 (the first row of shard 35) on, no text row matches any code in the index (best distance 180+ bits; random is about 512). Other text formats do not match either.

So the dense index covers the first 35,416,680 passages (85%). Keyword search covers all 41,488,110. To close the gap, re-embed shards 35–40 (about 6.1M passages) with mxbai on a GPU (about 1–2 hours), or switch to Plan B.

**krasserm check (2026-09-30, ADDENDUM §3.1).** [`krasserm/wikipedia-2023-11-en-embed-mxbai-int8-binary`](https://huggingface.co/datasets/krasserm/wikipedia-2023-11-en-embed-mxbai-int8-binary) @ `800b9e6` has mxbai codes for all 41,488,110 rows, in the same `_id` order as the Plan A text. But its codes differ from the verified prebuilt codes: on 288,052 shared rows the median Hamming distance is 209 of 512 bits (random is 256). Its input format is not ours, and its dataset card names no license. So we do not use it. `plan_a.py tail` and `extend` embed the 6,071,430 missing rows with `embed.py` instead.

**Full dense coverage (2026-09-30).** `embed.py` embedded the 6,071,430 missing rows on the 5060 Ti (2.6 h at ~640 passages/s), and `plan_a.py extend` assigned them to the existing 16,384 IVF lists. Dense search now covers all 41,488,110 `enwiki` passages and all 7,236,412 `enwiki-core` passages (was 85% and 89%). On 2,610 held-out NQ-open questions this raised hit@5 from 0.692 to 0.700 (docs/BENCHMARKS.md).

Result: `enwiki` has 5,854,898 articles and 41,488,110 passages in 12.8 GB.

`enwiki-core` (the starter pack, `plan_a.py subset`) keeps the lead passage of the 2,000,000 most-viewed articles and the full text of the 200,000 most-viewed ones: 7,236,412 passages in 2.75 GB. Dense codes cover 6,417,545 of them (89%).

**Qualified titles (2026-09-29).** `enrich` no longer turns a bare Wikidata label into a redirect to a qualified title: "Light Bulb" no longer leads to *Light Bulb (Abbott Elementary)*. The title "X (…)" shows that Wikipedia itself finds X ambiguous. The redirect tables of `enwiki` (2,208,959) and `enwiki-core` (1,054,162) were replaced in place.

**Short codes (2026-09-29).** `enrich` also keeps all-caps Wikidata aliases of 2–5 letters ("US", "UK", "NATO") as case-sensitive redirect keys, and the most-viewed claimant wins. The linker looks them up exactly, so "US" finds *United States* and never the film *Us*. The built `enwiki` and `enwiki-core` packs were patched in place with the same rows (+53,391 / +30,042 redirects) and their manifest hashes updated.

**Coverage finding (2026-09-29).** The Plan A text holds 5,851,754 articles, but it lacks many core articles: *Russia*, *Germany*, *Paris*, *Iran*, *Moon*, *Water*, *Nile*, *Malta*, *Eiffel Tower*. Of the 50,000 most-viewed titles in the 2026-04 pageviews, 24% are missing (some are events after 2023-11). Entity linking then falls through to wrong articles ("Malta" → *Malta, New York*). The keyword-only `enwiki-extra` pack fills the gap from finewiki (below).

**Number finding (2026-09-29).** The Plan A text drops the output of Wikipedia's `{{convert}}` templates, so many measurements are missing: "the Amazon River was  longer than the N…". Length, height and area questions suffer. finewiki expands templates.

Known quirk: some Plan A passages start mid-sentence (for example, the first *Aurora* passage starts "also commonly known as the northern lights…"). The source dataset cut the text that way.

## English Wikipedia articles missing from Plan A (`enwiki-extra`, replaced)

Replaced on 2026-10-02: the 2026 dump holds these articles.

- Input: [`HuggingFaceFW/finewiki`](https://huggingface.co/datasets/HuggingFaceFW/finewiki) `data/enwiki/*.parquet` @ `8bd13e72e6a002407649b3e898535f42ceb1aeb9` (2025-08, templates expanded). CC BY-SA 4.0.
- `scripts/fetch-finewiki-missing.sh` streams the 15 shards one at a time. It keeps only titles that are not in the `enwiki` pack and deletes each shard after filtering (peak disk ~2.5 GB, 5.7 GB kept).
- `chunk.py --pageviews data/raw/pageviews.sqlite` chunks them the same way as `simplewiki`, with real pageviews (99.7% matched). QIDs come from finewiki's `wikidata_id`.
- `enwiki-extra`: 1,026,478 articles, 6,593,073 passages, 2.46 GB. `enwiki-extra-core` (`plan_a.py subset --leads 1100000 --full 60000`): the leads of all of them plus the full text of the 60k most viewed; 2,234,425 passages, 1.05 GB.
- Dense codes (2026-09-30): `embed.py` embedded all 6,593,073 `enwiki-extra` passages on the 5060 Ti (7 h at ~250 passages/s; the finewiki passages are longer), 4,096 IVF lists. `plan_a.py subset` carries them into `enwiki-extra-core`. Both packs are no longer keyword-only.

## Simple English Wikipedia (`simplewiki`, dev pack)

- Input: [`HuggingFaceFW/finewiki`](https://huggingface.co/datasets/HuggingFaceFW/finewiki) `data/simplewiki/000_00000.parquet` @ `8bd13e72e6a002407649b3e898535f42ceb1aeb9` (265,299 pages, modified up to 2025-08). CC BY-SA 4.0.
- `wikimedia/structured-wikipedia` has no Simple English edition (only `enwiki` and `frwiki`), so the dev pack uses finewiki.
- Chunking (`chunk.py`): walk the Markdown headings, skip "See also", "References", "Related pages", "Other websites" and similar sections, pack paragraphs into 80–220 words, linearize tables up to 40 rows, and add one infobox passage per article. Disambiguation and "List of" pages are dropped. Result: 262,364 articles, 718,387 passages.
- Popularity: finewiki has no pageviews, so the dev pack uses the HTML size in KB as a stand-in.
- The dev pack has keyword search only. Its dense codes need mxbai document embeddings (a GPU job).

## Wikidata (`wikidata-facts`)

- Input: the `permutans/wikidata-claims`, `-links`, `-labels`, `-aliases` and `-claims_labels` tables on Hugging Face, built from the **Wikidata dump of 2026-05-07** (about 20 GB; `scripts/fetch-wikidata.sh` records the revisions in `data/raw/wikidata/REVISIONS`). License: CC0.
- Processing (`pipeline/commonplace_pipeline/wikidata.py`): keep items with an English Wikipedia sitelink and the 230 properties in `pipeline/configs/wikidata-props.tsv` (short labels, display order), drop deprecated statements, keep units and point-in-time qualifiers.
- Result (2026-09-30, all 7,449 claim chunks): 7,303,774 entities, 42,484,096 facts, 3.15 GB. The step also writes `title_qid.parquet` (7,499,539 English titles), which gives the English Wikipedia packs their QIDs.
- The first build (2026-09-29) had only 5,293 of 7,449 chunks: 5,375,749 entities and 31,455,594 facts, without France, metre, USD or Maltese. "How many times larger is Russia than France?" then got "1.43×"; now it gets 26.5×.
- The sitelinks upload of 2026-09-29 repeats rows across chunk files (17.8M rows, 10.3M items), so stage 2 deduplicates them. The sitelinks and labels files still stop at chunk 5,292 (labels also have 5,562–7,448), but the items checked (France, metre, USD, Maltese) have sitelinks.
- `plan_a.py enrich --title-qid` fills the missing QIDs of the English Wikipedia work directories by exact title and rebuilds the alias redirects; `packbuild meta` writes the new `meta.sqlite` into the built pack. `enwiki`: QIDs 4,214,209 → 5,686,153 of 5,854,898 articles, redirects 2,230,816 → 2,941,963.
- The app shows the facts that match the question first (for example "How tall…" shows elevation), then the rest in display order.

## Breadth packs

`scripts/fetch-extra-packs.sh` pins the sources; `pipeline/README.md` has the build commands. A pack without dense codes is keyword-only: the retriever then keeps its top 5 keyword hits in the rerank pool, so it competes with Wikipedia. `embed.py` adds the dense codes on the GPU (queue of 2026-09-30; the Dense column says which packs have them).

| Pack | Source | Snapshot | License | Articles / passages | Size | Dense |
|---|---|---|---|---|---|---|
| `wikivoyage-en` | Wikimedia dump `enwikivoyage-20260901` (XML, converted from wikitext) | 2026-09-01 | CC BY-SA 4.0 | 32,378 / 526,315 | 172 MB | queued |
| `stackexchange` | `common-pile/stackexchange` @ `5ec3aa2`, 52 sites (ADDENDUM §3.3 list) | 2024-12-31 | CC BY-SA 2.5/3.0/4.0 per post | 1,231,965 / 4,284,890 | 2.09 GB | queued |
| `openstax` | `HuggingFaceTB/openstax_paragraphs` | 2024-01-29 | CC BY 4.0 | 4,931 / 16,409 | 8 MB | yes |
| `arxiv-abs` | `librarian-bots/arxiv-metadata-snapshot` @ `1901619`, titles and abstracts | 2026-09-25 | CC0 1.0 | 3,182,867 / 3,314,444 | 3.11 GB | queued |
| `medlineplus` | MedlinePlus health topics XML (NLM) | 2026-09-26 | Public domain | 1,014 / 3,285 | 2 MB | yes |
| `wikibooks-en` | Kiwix ZIM `wikibooks_en_all_nopic_2026-04` | 2026-04 | CC BY-SA 4.0 | 83,521 / 1,010,601 | 363 MB | queued |
| `wikiquote-en` | Kiwix ZIM `wikiquote_en_all_nopic_2026-07` | 2026-07 | CC BY-SA 4.0 | 67,668 / 831,284 | 312 MB | queued |
| `wikiversity-en` | Kiwix ZIM `wikiversity_en_all_nopic_2026-08` | 2026-08 | CC BY-SA 4.0 | 42,937 / 449,719 | 152 MB | queued |
| `wikem-en` | Kiwix ZIM `wikem_en_all_nopic_2026-07`, English pages only | 2026-07 | CC BY-SA 3.0 | 4,236 / 41,867 | 9 MB | queued |
| `archwiki-en` | Kiwix ZIM `archlinux_en_all_maxi_2026-07`, English pages only | 2026-07 | GFDL 1.3 or later | 2,168 / 34,161 | 10 MB | queued |
| `devdocs-en` | Kiwix ZIMs `devdocs_en_*`: bash, C, C++, CSS, Docker, Git, Go, HTML, JavaScript, Kotlin, Nix, Node, NumPy, pandas, PostgreSQL, Python, React, Rust, SQLite, TypeScript | 2026-04 to 2026-08 | Per docset (devdocs.io/about) | 19,321 / 153,517 | 39 MB | queued |
| `cdc-travel` | CDC Travelers' Health destination pages (`cdc_travel.py fetch`, 20 s apart per robots.txt) | 2026-09-30 | Public domain (U.S. government work) | 244 / 13,503 | 2 MB | yes |
| `textbooks-en` | `common-pile/pressbooks_filtered` @ `1a1d3b5` and `libretexts_filtered` @ `70388bc`; front and back matter dropped | 2025-03 | CC BY, CC BY-SA, public domain or GFDL per chapter | 93,008 / 863,744 | 460 MB | queued |
| `wiktionary-en` | kaikki.org English Wiktionary extract of 2026-09-28; lemmas only (no inflected forms, alternative spellings, misspellings or proper names), articles not linkable | 2026-09-28 | CC BY-SA 4.0 and GFDL | 614,411 / 621,042 | 150 MB | queued |
| `factbook` | CIA World Factbook, final edition, via `factbook/factbook.json` @ `144d697` (one article per country, one section per category; articles not linkable) | 2026-02 | Public domain; the JSON mirror is CC0 | 261 / 6,505 | 3 MB | yes |

Deviation from PLAN.md §6.2: the common-pile Stack Exchange rows have no vote scores or accepted-answer flags. So the builder keeps threads by the number of distinct authors and drops comments by a heuristic, instead of "score ≥ 5 or accepted". Only the 2024-12 common-pile copy is used; the post-2024 official dumps are not. Practical sites (gardening, pets, money, law and 13 others) need 3 distinct authors, math 8, the rest 5.

The WikEM ZIM of 2026-07 holds 93,417 machine-translated subpages ("Sepsis/ru") marked as English; `zim.py --drop-translations` skips them. The ArchWiki ZIM holds its translations too, marked with their own `lang` attribute; `zim.py` skips every page whose content language is not English.

## Not used

The considered and rejected sources are listed in PLAN.md §6.1.
