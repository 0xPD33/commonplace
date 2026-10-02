//! "My documents": turns a document the user adds (text per page) into a small knowledge pack,
//! `packs/doc-<hash>/`. It uses the pack writers of `packbuild`, so the app reads it like any other pack.

use crate::encoders::QueryEncoder;
use crate::library::Library;
use crate::pack::{
    self, Counts, Embedder, FORMAT_VERSION, Manifest, PackType, PassageRecord, dense, frames, import,
    meta::{Article, MetaWriter},
    sparse::{SparseWriter, TANTIVY_VERSION},
};
use crate::text::sentences;
use anyhow::{Result, ensure};
use sha2::{Digest, Sha256};
use std::path::Path;

/// Largest document that is indexed.
pub const MAX_PASSAGES: usize = 5_000;
const MIN_WORDS: usize = 80;
const MAX_WORDS: usize = 220;
const MERGE_MAX: usize = 260;

fn words(s: &str) -> usize {
    s.split_whitespace().count()
}

/// Paragraphs: runs of non-blank lines, joined with spaces.
fn paragraphs(page: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut para: Vec<&str> = Vec::new();
    for line in page.lines().map(str::trim).chain(std::iter::once("")) {
        if line.is_empty() {
            if !para.is_empty() {
                out.push(para.join(" "));
                para.clear();
            }
        } else {
            para.push(line);
        }
    }
    out
}

/// Passage rules of `pipeline/commonplace_pipeline/chunk.py` (`pack_blocks`): paragraphs of at most 220
/// words, long ones split at sentences; short neighbours merge up to 220 words (the last one up to 260).
fn pack_blocks(blocks: Vec<String>) -> Vec<String> {
    let mut units: Vec<String> = Vec::new();
    for b in blocks {
        if words(&b) <= MAX_WORDS {
            units.push(b);
            continue;
        }
        let mut cur: Vec<&str> = Vec::new();
        let mut n = 0;
        // A sentence over the limit (a table, text without punctuation) is cut at the word limit.
        let pieces: Vec<String> = sentences(&b)
            .into_iter()
            .flat_map(|s| s.split_whitespace().collect::<Vec<_>>().chunks(MAX_WORDS).map(|c| c.join(" ")).collect::<Vec<_>>())
            .collect();
        for s in &pieces {
            let w = words(s);
            if !cur.is_empty() && n + w > MAX_WORDS {
                units.push(cur.join(" "));
                cur.clear();
                n = 0;
            }
            cur.push(s);
            n += w;
        }
        if !cur.is_empty() {
            units.push(cur.join(" "));
        }
    }
    let mut out: Vec<String> = Vec::new();
    let mut cur = String::new();
    for u in units {
        if cur.is_empty() {
            cur = u;
        } else if words(&cur) < MIN_WORDS && words(&cur) + words(&u) <= MAX_WORDS {
            cur = format!("{cur}\n{u}");
        } else {
            out.push(std::mem::replace(&mut cur, u));
        }
    }
    if !cur.is_empty() {
        match out.last_mut() {
            Some(last) if words(&cur) < MIN_WORDS && words(last) + words(&cur) <= MERGE_MAX => *last = format!("{last}\n{cur}"),
            _ => out.push(cur),
        }
    }
    out
}

/// Passages of a document in reading order, as (1-based page number, text). A passage never spans pages.
pub fn chunk(pages: &[String]) -> Vec<(usize, String)> {
    pages.iter().enumerate().flat_map(|(i, p)| pack_blocks(paragraphs(p)).into_iter().map(move |t| (i + 1, t))).collect()
}

/// Build the pack of a document and install it into `<library_dir>/packs/`. Returns the pack id.
/// `progress(done, total)` counts passages that are embedded. The caller reloads the library.
pub fn build(library_dir: &Path, title: &str, pages: &[String], enc: &QueryEncoder, progress: &mut dyn FnMut(usize, usize)) -> Result<String> {
    let title = Some(title.trim()).filter(|t| !t.is_empty()).unwrap_or("Untitled document");
    let passages = chunk(pages);
    ensure!(!passages.is_empty(), "This document has no text to search. A scanned PDF needs a text layer first.");
    ensure!(
        passages.len() <= MAX_PASSAGES,
        "This document is too long: {} passages, the limit is {MAX_PASSAGES}. Add a shorter document or split it.",
        passages.len()
    );

    let mut h = Sha256::new();
    for s in std::iter::once(title).chain(pages.iter().map(String::as_str)) {
        h.update(s.as_bytes());
        h.update([0]);
    }
    let pack_id = format!("doc-{}", &hex::encode(h.finalize())[..10]);
    let packs_dir = Library::packs_dir(library_dir);
    std::fs::create_dir_all(&packs_dir)?;
    let dest = packs_dir.join(&pack_id);
    ensure!(!dest.exists(), "This document is already in your library.");
    // Library::open ignores dot-directories, so a half-built pack is never loaded.
    let tmp = packs_dir.join(format!(".building-{pack_id}"));
    let _ = std::fs::remove_dir_all(&tmp);
    std::fs::create_dir_all(&tmp)?;
    let res = write(&tmp, &pack_id, title, &passages, enc, progress).and_then(|()| Ok(std::fs::rename(&tmp, &dest)?));
    if res.is_err() {
        let _ = std::fs::remove_dir_all(&tmp);
    }
    res.map(|()| pack_id)
}

fn write(out: &Path, pack_id: &str, title: &str, passages: &[(usize, String)], enc: &QueryEncoder, progress: &mut dyn FnMut(usize, usize)) -> Result<()> {
    let n = passages.len();
    let date = pack::today();

    // The document is one article, so its passages have consecutive ids and the article view shows all pages.
    let mut mw = MetaWriter::create(&out.join("meta.sqlite"))?;
    let article = Article { id: 0, title: title.into(), qid: None, popularity: 0, url_title: None, oneliner: None, first_passage: 0, n_passages: n as u32 };
    mw.add_article(&article, true)?;
    mw.add_source(title, "", "user document", &date)?;
    mw.finish()?;

    // Too few samples to train a dictionary: frames use an empty one.
    let mut fw = frames::FrameWriter::create(&out.join("store"), Vec::new(), 15)?;
    let mut sw = SparseWriter::create(&out.join("tantivy"), 150_000_000)?;
    for (i, (page, text)) in passages.iter().enumerate() {
        let section = format!("p. {page}");
        sw.add(i as u32, title, &section, text, "")?;
        fw.push(PassageRecord { article_id: 0, ordinal: i as u16, section_path: section, text: text.clone() }.encode())?;
    }
    fw.finish()?;
    sw.finish()?;

    // One IVF list: a document is small enough to scan whole.
    let texts: Vec<String> = passages.iter().map(|(_, t)| format!("{title}\n{t}")).collect();
    let mut done = 0;
    let vecs = enc.encode_docs(&texts, &mut |k| {
        done += k;
        progress(done, n);
    })?;
    let dims = enc.dims;
    let mut codes = Vec::with_capacity(n * dims / 8);
    let mut centroid = vec![0f32; dims];
    for v in &vecs {
        codes.extend(dense::pack_bits(v));
        centroid.iter_mut().zip(v).for_each(|(c, x)| *c += x);
    }
    let norm = centroid.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
    centroid.iter_mut().for_each(|c| *c /= norm);
    dense::write(&out.join("dense"), dims as u32, &centroid, &codes, &vec![0; n], None)?;

    let (files, size) = import::file_entries(out)?;
    let manifest = Manifest {
        format_version: FORMAT_VERSION,
        pack_id: pack_id.into(),
        pack_type: PackType::Knowledge,
        title: title.into(),
        snapshot_date: date.clone(),
        build_date: date,
        replaces: vec![],
        license: "user document".into(),
        attribution: "Added by you".into(),
        counts: Some(Counts { articles: 1, passages: n as u64 }),
        embedder: Some(Embedder { doc: "MongoDB/mdbr-leaf-mt".into(), query: "MongoDB/mdbr-leaf-mt".into(), dims: dims as u32, encoding: "binary-sign-mrl".into() }),
        tantivy_version: Some(TANTIVY_VERSION.into()),
        model: None,
        user_document: true,
        files,
        size_bytes: size,
    };
    std::fs::write(out.join("manifest.json"), serde_json::to_vec_pretty(&manifest)?)?;
    Ok(())
}
