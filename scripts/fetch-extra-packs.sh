#!/usr/bin/env bash
# Download the sources of the breadth packs and the Wikipedia pageviews into data/raw/, pinned to fixed revisions.
# Skips files already on disk.
# Usage: scripts/fetch-extra-packs.sh [wikivoyage|stackexchange|openstax|arxiv|medlineplus|textbooks|wiktionary|factbook|zim|pageviews ...]
# Without arguments it fetches wikivoyage stackexchange openstax arxiv medlineplus.
set -euo pipefail
cd "$(dirname "$0")/.."
HF=https://huggingface.co/datasets
WIKIVOYAGE_DUMP=20260901
SE_REV=5ec3aa2e65b7c61b7c18f1ff18ccbc4d3279a23c
SE_SITES=(travel.stackexchange.com outdoors.stackexchange.com cooking.stackexchange.com diy.stackexchange.com
  physics.stackexchange.com math.stackexchange.com stats.stackexchange.com superuser.com askubuntu.com
  unix.stackexchange.com bicycles.stackexchange.com mechanics.stackexchange.com
  $(printf '%s.stackexchange.com ' gardening money law expatriates pets fitness history biology chemistry astronomy \
    earthscience health skeptics aviation parenting sustainability economics politics philosophy space hsm lifehacks \
    woodworking homebrew coffee crafts genealogy linguistics english movies scifi music photo sports boardgames chess \
    interpersonal academia workplace))
OPENSTAX_REV=d336e6a20db3540f7c96dae6bb4a9f1297b70f39
ARXIV_REV=1901619dd2954ac88ec84ac1a4ab151b29dd82b5
MEDLINEPLUS_DATE=2026-09-26
PAGEVIEWS_REV=40a94fda39044e78a0fc3d92c07d4f2bef4c4587  # NeuML/wikipedia-20260401: pages(title, views), the popularity prior of the Wikipedia build
PRESSBOOKS_REV=1a1d3b50d77f834370f8eb4c0d174668dd1676bb
LIBRETEXTS_REV=70388bca52b4a93515e14b1d56618fd7944988fd
ZIMS=(wikibooks/wikibooks_en_all_nopic_2026-04.zim wikiquote/wikiquote_en_all_nopic_2026-07.zim
  wikiversity/wikiversity_en_all_nopic_2026-08.zim other/wikem_en_all_nopic_2026-07.zim other/archlinux_en_all_maxi_2026-07.zim
  $(printf 'devdocs/devdocs_en_%s.zim ' bash_2026-04 c_2026-07 cpp_2026-07 css_2026-07 docker_2026-07 git_2026-07 go_2026-07 \
    html_2026-07 javascript_2026-07 kotlin_2026-07 nix_2026-07 node_2026-08 numpy_2026-07 pandas_2026-07 postgresql_2026-08 \
    python_2026-08 react_2026-05 rust_2026-07 sqlite_2026-07 typescript_2026-07))

get() {  # url dest
  [ -s "$2" ] && return
  mkdir -p "$(dirname "$2")"
  curl -fsSL --retry 5 -C - -o "$2.part" "$1" && mv "$2.part" "$2"
}
export -f get

for src in "${@:-wikivoyage stackexchange openstax arxiv medlineplus}"; do for s in $src; do case $s in
  wikivoyage)
    for f in pages-articles.xml.bz2 page_props.sql.gz; do
      get "https://dumps.wikimedia.org/enwikivoyage/$WIKIVOYAGE_DUMP/enwikivoyage-$WIKIVOYAGE_DUMP-$f" \
        "data/raw/wikivoyage/enwikivoyage-$WIKIVOYAGE_DUMP-$f"
    done ;;
  stackexchange)
    for site in "${SE_SITES[@]}"; do
      curl -fsS --retry 8 --retry-all-errors --retry-delay 10 "https://huggingface.co/api/datasets/common-pile/stackexchange/tree/$SE_REV/$site/documents" |
        jq -r '.[] | select(.type=="file") | .path' |
        while read -r p; do echo "$HF/common-pile/stackexchange/resolve/$SE_REV/$p data/raw/stackexchange/$p"; done
    done | xargs -P 6 -n 2 bash -c 'get "$0" "$1"' ;;
  openstax)
    get "$HF/HuggingFaceTB/openstax_paragraphs/resolve/$OPENSTAX_REV/openstax_books.jsonl" data/raw/openstax/openstax_books.jsonl ;;
  arxiv)
    for i in $(seq -f %05g 0 10); do
      get "$HF/librarian-bots/arxiv-metadata-snapshot/resolve/$ARXIV_REV/data/train-$i-of-00011.parquet" \
        "data/raw/arxiv/train-$i-of-00011.parquet"
    done ;;
  medlineplus)
    get "https://medlineplus.gov/xml/mplus_topics_$MEDLINEPLUS_DATE.xml" "data/raw/medlineplus/mplus_topics_$MEDLINEPLUS_DATE.xml" ;;
  textbooks)
    get "$HF/common-pile/pressbooks_filtered/resolve/$PRESSBOOKS_REV/pressbooks-0000.json.gz" data/raw/common-pile/pressbooks-0000.json.gz
    get "$HF/common-pile/libretexts_filtered/resolve/$LIBRETEXTS_REV/libretexts-0000.json.gz" data/raw/common-pile/libretexts-0000.json.gz ;;
  wiktionary)  # kaikki.org has no revisions; the file of 2026-09-28 (Last-Modified) is 3,335,559,018 bytes
    get https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl data/raw/kaikki/kaikki.org-dictionary-English.jsonl ;;
  factbook)
    get https://codeload.github.com/factbook/factbook.json/tar.gz/144d6977b2b01ac1cbd220de754c0a005616760b data/raw/factbook/factbook-144d697.tar.gz
    tar -xzf data/raw/factbook/factbook-144d697.tar.gz -C data/raw/factbook ;;
  pageviews)
    get "$HF/NeuML/wikipedia-20260401/resolve/$PAGEVIEWS_REV/pageviews.sqlite" data/raw/pageviews.sqlite ;;
  zim)
    for z in "${ZIMS[@]}"; do get "https://download.kiwix.org/zim/$z" "data/raw/zim/${z#*/}"; done ;;
  *) echo "unknown source: $s" >&2; exit 1 ;;
esac; done; done
