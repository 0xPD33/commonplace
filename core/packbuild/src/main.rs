//! packbuild: writes Commonplace packs from pipeline Parquet, splits them into distribution parts,
//! and wraps GGUF models as model packs. Input schema: docs/PACKS.md.

use anyhow::{Context, Result, bail, ensure};
use arrow_array::cast::AsArray;
use arrow_array::types::{Float64Type, UInt16Type, UInt32Type, UInt64Type};
use arrow_array::{Array, RecordBatch};
use clap::{Args, Parser, Subcommand};
use commonplace_core::pack::{
    self, Counts, Embedder, Fact, FileEntry, FORMAT_VERSION, Manifest, ModelInfo, ModelRole, PackType, PassageRecord, dense, frames,
    import, meta, sparse,
};
use commonplace_core::tools::wikidata;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

#[derive(Parser)]
#[command(about = "Build Commonplace packs")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

/// License files that travel with a pack: listed in `manifest.json` and verified like every other file.
#[derive(Args)]
struct NoticeArgs {
    /// Copied into the pack as `NOTICE.txt` (credits, license names and texts).
    #[arg(long)]
    notice: Option<PathBuf>,
    /// Copied into the pack as `CREDITS.txt` (`CREDITS.txt.zst` for a .zst file): per-work credits, such as one line per book.
    #[arg(long)]
    credits: Option<PathBuf>,
}

#[derive(Subcommand)]
enum Cmd {
    /// Build a knowledge pack from articles/passages Parquet (+ optional questions, cards, dense).
    Build {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        pack_id: String,
        #[arg(long)]
        title: String,
        #[arg(long)]
        snapshot: String,
        #[arg(long, default_value = "CC BY-SA 4.0")]
        license: String,
        #[arg(long)]
        attribution: String,
        #[arg(long, value_delimiter = ',')]
        replaces: Vec<String>,
        #[arg(long, default_value = "mixedbread-ai/mxbai-embed-large-v1")]
        embedder_doc: String,
        #[arg(long, default_value = "MongoDB/mdbr-leaf-mt")]
        embedder_query: String,
        #[arg(long, default_value_t = 15)]
        zstd_level: i32,
        /// Skip the tantivy index (dense-only pack).
        #[arg(long)]
        no_sparse: bool,
        #[command(flatten)]
        notices: NoticeArgs,
    },
    /// Add or replace NOTICE.txt / CREDITS.txt of a built pack and optionally fix its license or attribution.
    Notice {
        #[arg(long)]
        pack: PathBuf,
        #[command(flatten)]
        notices: NoticeArgs,
        #[arg(long)]
        license: Option<String>,
        #[arg(long)]
        attribution: Option<String>,
    },
    /// Rewrite meta.sqlite of a built knowledge pack from `<input>` articles/redirects (same articles).
    Meta {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        pack: PathBuf,
    },
    /// Replace the dense index of a built knowledge pack with `<input>/dense` (same passages).
    Dense {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        pack: PathBuf,
        #[arg(long, default_value = "mixedbread-ai/mxbai-embed-large-v1")]
        embedder_doc: String,
        #[arg(long, default_value = "MongoDB/mdbr-leaf-mt")]
        embedder_query: String,
    },
    /// Build the wikidata-facts pack from props/entities/facts Parquet.
    Wikidata {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        snapshot: String,
        #[command(flatten)]
        notices: NoticeArgs,
    },
    /// Wrap a GGUF file, or a directory of model files (`--dir` with `--file`), as a model pack.
    Model {
        #[arg(long, required_unless_present = "dir", conflicts_with = "dir")]
        gguf: Option<PathBuf>,
        /// Copy every file of this directory into the pack. `--file` names the main one.
        #[arg(long, requires = "file")]
        dir: Option<PathBuf>,
        #[arg(long)]
        file: Option<String>,
        #[arg(long)]
        out: PathBuf,
        #[arg(long)]
        pack_id: String,
        #[arg(long)]
        title: String,
        #[arg(long, value_parser = ["llm-fast", "llm-small", "llm-deep", "stt"])]
        role: String,
        #[arg(long)]
        hf_repo: String,
        #[arg(long)]
        revision: String,
        #[arg(long)]
        license: String,
        #[arg(long, default_value_t = 4096)]
        n_ctx: u32,
        /// Hard-link instead of copying the GGUF.
        #[arg(long)]
        link: bool,
        /// Defaults to the Hugging Face repo.
        #[arg(long)]
        attribution: Option<String>,
        #[command(flatten)]
        notices: NoticeArgs,
    },
    /// Write a pack directory as ≤ part-size tar parts plus `<pack_id>.pack.json`, or as one `<pack_id>.tar` with --single.
    Split {
        #[arg(long)]
        pack: PathBuf,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 2_000_000_000, conflicts_with = "single")]
        part_size: u64,
        /// One file `<pack_id>.tar` and no `.pack.json`.
        #[arg(long)]
        single: bool,
    },
    /// Write several pack directories, in the given order, as one tar stream `--out <file>.tar` (a bundle).
    Bundle {
        #[arg(long, required = true, num_args = 1..)]
        pack: Vec<PathBuf>,
        #[arg(long)]
        out: PathBuf,
    },
    /// Re-hash an installed pack against its manifest.
    Verify {
        #[arg(long)]
        pack: PathBuf,
    },
}

fn batches(path: &Path) -> Result<impl Iterator<Item = Result<RecordBatch>>> {
    let f = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let r = ParquetRecordBatchReaderBuilder::try_new(f)?.with_batch_size(16_384).build()?;
    Ok(r.map(|b| b.map_err(Into::into)))
}

fn col<'a>(b: &'a RecordBatch, name: &str) -> Result<&'a dyn Array> {
    b.column_by_name(name).map(|c| c.as_ref()).with_context(|| format!("missing column {name}"))
}

fn str_at(a: &dyn Array, i: usize) -> Option<String> {
    if a.is_null(i) {
        return None;
    }
    match a.data_type() {
        arrow_schema::DataType::Utf8 => Some(a.as_string::<i32>().value(i).to_string()),
        arrow_schema::DataType::LargeUtf8 => Some(a.as_string::<i64>().value(i).to_string()),
        arrow_schema::DataType::Utf8View => Some(a.as_string_view().value(i).to_string()),
        t => panic!("expected a string column, got {t}"),
    }
}

fn u32_at(a: &dyn Array, i: usize) -> u32 {
    match a.data_type() {
        arrow_schema::DataType::UInt32 => a.as_primitive::<UInt32Type>().value(i),
        arrow_schema::DataType::UInt64 => a.as_primitive::<UInt64Type>().value(i) as u32,
        arrow_schema::DataType::Int64 => a.as_primitive::<arrow_array::types::Int64Type>().value(i) as u32,
        arrow_schema::DataType::Int32 => a.as_primitive::<arrow_array::types::Int32Type>().value(i) as u32,
        t => panic!("expected an integer column, got {t}"),
    }
}

fn read_raw<T: Copy>(path: &Path, from: fn(&[u8]) -> T, width: usize) -> Result<Vec<T>> {
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    ensure!(bytes.len() % width == 0, "{}: size not a multiple of {width}", path.display());
    Ok(bytes.chunks_exact(width).map(from).collect())
}

struct ArticleRow {
    title: String,
}

fn build(
    input: &Path,
    out: &Path,
    pack_id: &str,
    title: &str,
    snapshot: &str,
    license: &str,
    attribution: &str,
    replaces: Vec<String>,
    embedder: Option<Embedder>,
    level: i32,
    no_sparse: bool,
) -> Result<()> {
    let t0 = Instant::now();
    if out.exists() {
        std::fs::remove_dir_all(out)?;
    }
    std::fs::create_dir_all(out)?;
    let articles = write_meta(input, &out.join("meta.sqlite"), title, license, snapshot)?;
    eprintln!("meta: {} articles ({:.1}s)", articles.len(), t0.elapsed().as_secs_f64());

    // Optional doc2query questions, keyed by passage.
    let mut questions: HashMap<u32, String> = HashMap::new();
    if input.join("questions.parquet").exists() {
        for b in batches(&input.join("questions.parquet"))? {
            let b = b?;
            let (pid, q) = (col(&b, "passage_id")?, col(&b, "questions")?);
            for i in 0..b.num_rows() {
                questions.insert(u32_at(pid, i), str_at(q, i).unwrap_or_default());
            }
        }
        eprintln!("questions: {} passages", questions.len());
    }

    // Passages → store (+ tantivy).
    let passages_path = input.join("passages.parquet");
    let mut samples: Vec<Vec<u8>> = Vec::new();
    'outer: for b in batches(&passages_path)? {
        let b = b?;
        let (aid, ord, sec, text) = (col(&b, "article_id")?, col(&b, "ordinal")?, col(&b, "section_path")?, col(&b, "text")?);
        for i in (0..b.num_rows()).step_by(3) {
            samples.push(
                PassageRecord {
                    article_id: u32_at(aid, i),
                    ordinal: ord.as_primitive::<UInt16Type>().value(i),
                    section_path: str_at(sec, i).unwrap_or_default(),
                    text: str_at(text, i).unwrap_or_default(),
                }
                .encode(),
            );
            if samples.len() >= 30_000 {
                break 'outer;
            }
        }
    }
    let dict = frames::train_dict(&samples, 112_640)?;
    drop(samples);
    let mut fw = frames::FrameWriter::create(&out.join("store"), dict, level)?;
    let mut sw = if no_sparse { None } else { Some(sparse::SparseWriter::create(&out.join("tantivy"), 1_000_000_000)?) };
    let mut n: u32 = 0;
    for b in batches(&passages_path)? {
        let b = b?;
        let (pid, aid, ord, sec, text) =
            (col(&b, "passage_id")?, col(&b, "article_id")?, col(&b, "ordinal")?, col(&b, "section_path")?, col(&b, "text")?);
        for i in 0..b.num_rows() {
            ensure!(u32_at(pid, i) == n, "passage ids must be dense and sorted (got {} at {n})", u32_at(pid, i));
            let rec = PassageRecord {
                article_id: u32_at(aid, i),
                ordinal: ord.as_primitive::<UInt16Type>().value(i),
                section_path: str_at(sec, i).unwrap_or_default(),
                text: str_at(text, i).unwrap_or_default(),
            };
            if let Some(w) = sw.as_mut() {
                let t = &articles.get(rec.article_id as usize).context("passage references unknown article")?.title;
                w.add(n, t, &rec.section_path, &rec.text, questions.get(&n).map(String::as_str).unwrap_or(""))?;
            }
            fw.push(rec.encode())?;
            n += 1;
        }
        if n % 1_000_000 < 16_384 {
            eprintln!("passages: {n} ({:.0}s)", t0.elapsed().as_secs_f64());
        }
    }
    fw.finish()?;
    if let Some(w) = sw {
        w.finish()?;
    }
    eprintln!("store+tantivy: {n} passages ({:.1}s)", t0.elapsed().as_secs_f64());

    // Optional fact cards → cards/ (one record per passage).
    if input.join("cards.parquet").exists() {
        let mut by_pid: HashMap<u32, Vec<Fact>> = HashMap::new();
        for b in batches(&input.join("cards.parquet"))? {
            let b = b?;
            let (pid, fact, src) = (col(&b, "passage_id")?, col(&b, "fact")?, col(&b, "source_ids")?);
            for i in 0..b.num_rows() {
                let ids = str_at(src, i).unwrap_or_default().split(',').filter_map(|x| x.trim().parse().ok()).collect();
                by_pid.entry(u32_at(pid, i)).or_default().push(Fact { text: str_at(fact, i).unwrap_or_default(), passage_ids: ids });
            }
        }
        let samples: Vec<Vec<u8>> = by_pid.values().take(20_000).map(|f| pack::encode_facts(f)).collect();
        let dict = frames::train_dict(&samples, 65_536)?;
        let mut cw = frames::FrameWriter::create(&out.join("cards"), dict, level)?;
        for p in 0..n {
            cw.push(by_pid.get(&p).map(|f| pack::encode_facts(f)).unwrap_or_default())?;
        }
        cw.finish()?;
        eprintln!("cards: {} passages with facts", by_pid.len());
    }

    // Optional dense index.
    let dd = input.join("dense");
    let dims = if dd.exists() { write_dense(&dd, &out.join("dense"), n)? } else { 512 };

    let (files, size) = import::file_entries(out)?;
    let manifest = Manifest {
        format_version: FORMAT_VERSION,
        pack_id: pack_id.into(),
        pack_type: PackType::Knowledge,
        title: title.into(),
        snapshot_date: snapshot.into(),
        build_date: pack::today(),
        replaces,
        license: license.into(),
        attribution: attribution.into(),
        counts: Some(Counts { articles: articles.len() as u64, passages: n as u64 }),
        embedder: embedder.map(|mut e| {
            e.dims = dims;
            e
        }),
        tantivy_version: (!no_sparse).then(|| sparse::TANTIVY_VERSION.to_string()),
        model: None,
        user_document: false,
        files,
        size_bytes: size,
    };
    std::fs::write(out.join("manifest.json"), serde_json::to_vec_pretty(&manifest)?)?;
    eprintln!("done: {} ({:.2} GB, {:.1}s)", out.display(), size as f64 / 1e9, t0.elapsed().as_secs_f64());
    Ok(())
}

/// Writes the dense index from pipeline files (`dd`: info.json, codes.u8, assign.u32, centroids.f32,
/// optional ids.u32) into `out` for a pack of `n` passages. Returns the code dimensions.
fn write_dense(dd: &Path, out: &Path, n: u32) -> Result<u32> {
    let info: serde_json::Value = serde_json::from_slice(&std::fs::read(dd.join("info.json"))?)?;
    let dims = info["dims"].as_u64().context("dense/info.json: dims")? as u32;
    let codes = std::fs::read(dd.join("codes.u8"))?;
    let assign = read_raw(&dd.join("assign.u32"), |b| u32::from_le_bytes(b.try_into().unwrap()), 4)?;
    let cents = read_raw(&dd.join("centroids.f32"), |b| f32::from_le_bytes(b.try_into().unwrap()), 4)?;
    // Dense may cover a prefix of the passages (Plan A: only the rows verified against the text).
    ensure!(assign.len() <= n as usize, "dense has {} codes but pack has only {n} passages", assign.len());
    let ids = if dd.join("ids.u32").exists() {
        let v = read_raw(&dd.join("ids.u32"), |b| u32::from_le_bytes(b.try_into().unwrap()), 4)?;
        ensure!(v.iter().all(|&i| i < n), "dense ids.u32 references a passage beyond {n}");
        Some(v)
    } else {
        None
    };
    let di = dense::write(out, dims, &cents, &codes, &assign, ids.as_deref())?;
    eprintln!("dense: {} codes in {} lists", di.n_codes, di.n_lists);
    Ok(dims)
}

/// Replaces the dense index of a built knowledge pack and rewrites its manifest (file hashes, embedder).
/// The passage store and the tantivy index stay as they are, so `input` must come from the same passages.
fn replace_dense(input: &Path, pack: &Path, embedder: Embedder) -> Result<()> {
    let t0 = Instant::now();
    let mut m: Manifest = serde_json::from_slice(&std::fs::read(pack.join("manifest.json"))?)?;
    let n = m.counts.as_ref().context("manifest has no counts")?.passages as u32;
    let out = pack.join("dense");
    if out.exists() {
        std::fs::remove_dir_all(&out)?;
    }
    let dims = write_dense(&input.join("dense"), &out, n)?;
    let (files, size) = import::file_entries(pack)?;
    m.embedder = Some(Embedder { dims, ..embedder });
    m.files = files;
    m.size_bytes = size;
    m.build_date = pack::today();
    std::fs::write(pack.join("manifest.json"), serde_json::to_vec_pretty(&m)?)?;
    eprintln!("done: {} ({:.2} GB, {:.1}s)", pack.display(), size as f64 / 1e9, t0.elapsed().as_secs_f64());
    Ok(())
}

/// Articles and redirects Parquet → meta.sqlite.
fn write_meta(input: &Path, path: &Path, title: &str, license: &str, snapshot: &str) -> Result<Vec<ArticleRow>> {
    let mut mw = meta::MetaWriter::create(path)?;
    let mut articles: Vec<ArticleRow> = Vec::new();
    for b in batches(&input.join("articles.parquet"))? {
        let b = b?;
        let (id, ti, qid, pop, url, one, fp, np) = (
            col(&b, "article_id")?,
            col(&b, "title")?,
            col(&b, "qid")?,
            col(&b, "popularity")?,
            col(&b, "url_title")?,
            col(&b, "oneliner")?,
            col(&b, "first_passage")?,
            col(&b, "n_passages")?,
        );
        let linkable = b.column_by_name("linkable").map(|c| c.as_boolean().clone());
        for i in 0..b.num_rows() {
            let a = meta::Article {
                id: u32_at(id, i),
                title: str_at(ti, i).unwrap_or_default(),
                qid: str_at(qid, i).filter(|s| !s.is_empty()),
                popularity: pop.as_primitive::<UInt64Type>().value(i),
                url_title: str_at(url, i),
                oneliner: str_at(one, i),
                first_passage: u32_at(fp, i),
                n_passages: u32_at(np, i),
            };
            ensure!(a.id as usize == articles.len(), "article ids must be dense and sorted (got {} at {})", a.id, articles.len());
            mw.add_article(&a, linkable.as_ref().is_none_or(|l| l.value(i)))?;
            articles.push(ArticleRow { title: a.title });
        }
    }
    if input.join("redirects.parquet").exists() {
        for b in batches(&input.join("redirects.parquet"))? {
            let b = b?;
            let (from, to) = (col(&b, "from_title")?, col(&b, "article_id")?);
            for i in 0..b.num_rows() {
                mw.add_redirect(&str_at(from, i).unwrap_or_default(), u32_at(to, i))?;
            }
        }
    }
    mw.add_source(title, "", license, snapshot)?;
    mw.finish()?;
    Ok(articles)
}

/// Rewrites meta.sqlite of a built knowledge pack from new articles/redirects Parquet (same articles, e.g.
/// after `plan_a.py enrich` added QIDs or redirects) and updates the manifest hashes.
fn replace_meta(input: &Path, pack: &Path) -> Result<()> {
    let t0 = Instant::now();
    let mut m: Manifest = serde_json::from_slice(&std::fs::read(pack.join("manifest.json"))?)?;
    let tmp = pack.join("meta.sqlite.new");
    let _ = std::fs::remove_file(&tmp);
    let n = write_meta(input, &tmp, &m.title, &m.license, &m.snapshot_date)?.len() as u64;
    ensure!(m.counts.as_ref().is_some_and(|c| c.articles == n), "the pack has {:?} articles, the input {n}", m.counts);
    std::fs::rename(&tmp, pack.join("meta.sqlite"))?;
    let (files, size) = import::file_entries(pack)?;
    m.files = files;
    m.size_bytes = size;
    m.build_date = pack::today();
    std::fs::write(pack.join("manifest.json"), serde_json::to_vec_pretty(&m)?)?;
    eprintln!("done: {} ({:.1}s)", pack.display(), t0.elapsed().as_secs_f64());
    Ok(())
}

/// Puts NOTICE.txt / CREDITS.txt into a built pack and lists them in the manifest. Other files keep their
/// hashes, so this is fast for large packs.
fn add_notices(pack: &Path, n: &NoticeArgs, license: Option<&str>, attribution: Option<&str>) -> Result<()> {
    let mut m = Manifest::read(pack)?;
    let credits_name = if n.credits.as_ref().is_some_and(|p| p.extension().is_some_and(|e| e == "zst")) { "CREDITS.txt.zst" } else { "CREDITS.txt" };
    for (src, name) in [(&n.notice, "NOTICE.txt"), (&n.credits, credits_name)] {
        let Some(src) = src else { continue };
        let dest = pack.join(name);
        std::fs::copy(src, &dest).with_context(|| format!("copy {}", src.display()))?;
        let bytes = std::fs::metadata(&dest)?.len();
        if let Some(i) = m.files.iter().position(|f| f.path == name) {
            m.size_bytes -= m.files.remove(i).bytes;
        }
        m.files.push(FileEntry { path: name.into(), bytes, sha256: pack::sha256_file(&dest)? });
        m.size_bytes += bytes;
    }
    m.files.sort_by(|a, b| a.path.cmp(&b.path));
    if let Some(l) = license {
        m.license = l.into();
    }
    if let Some(a) = attribution {
        m.attribution = a.into();
    }
    std::fs::write(pack.join("manifest.json"), serde_json::to_vec_pretty(&m)?)?;
    Ok(())
}

fn build_wikidata(input: &Path, out: &Path, snapshot: &str) -> Result<()> {
    if out.exists() {
        std::fs::remove_dir_all(out)?;
    }
    std::fs::create_dir_all(out)?;
    let db = out.join("wikidata.sqlite");
    let conn = rusqlite::Connection::open(&db)?;
    conn.execute_batch("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")?;
    conn.execute_batch(wikidata::SCHEMA)?;
    conn.execute_batch("BEGIN")?;
    for b in batches(&input.join("props.parquet"))? {
        let b = b?;
        let (pid, label, pr) = (col(&b, "pid")?, col(&b, "label")?, col(&b, "priority")?);
        for i in 0..b.num_rows() {
            conn.execute("INSERT INTO props VALUES (?1, ?2, ?3)", rusqlite::params![str_at(pid, i), str_at(label, i), u32_at(pr, i)])?;
        }
    }
    for b in batches(&input.join("entities.parquet"))? {
        let b = b?;
        let (qid, label, aliases) = (col(&b, "qid")?, col(&b, "label")?, col(&b, "aliases")?);
        let mut st = conn.prepare_cached("INSERT OR IGNORE INTO entities VALUES (?1, ?2, ?3)")?;
        for i in 0..b.num_rows() {
            st.execute(rusqlite::params![str_at(qid, i), str_at(label, i), str_at(aliases, i)])?;
        }
    }
    let mut n = 0u64;
    for b in batches(&input.join("facts.parquet"))? {
        let b = b?;
        let (qid, pid, num, text, unit, when, rank) =
            (col(&b, "qid")?, col(&b, "pid")?, col(&b, "value_num")?, col(&b, "value_text")?, col(&b, "unit")?, col(&b, "point_in_time")?, col(&b, "rank")?);
        let mut st = conn.prepare_cached("INSERT INTO facts VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)")?;
        for i in 0..b.num_rows() {
            let v: Option<f64> = (!num.is_null(i)).then(|| num.as_primitive::<Float64Type>().value(i));
            st.execute(rusqlite::params![str_at(qid, i), str_at(pid, i), v, str_at(text, i), str_at(unit, i), str_at(when, i), u32_at(rank, i)])?;
            n += 1;
        }
    }
    conn.execute_batch("COMMIT")?;
    conn.execute_batch(wikidata::INDEXES)?;
    conn.execute_batch("ANALYZE; VACUUM;")?;
    drop(conn);
    let (files, size) = import::file_entries(out)?;
    let m = Manifest {
        format_version: FORMAT_VERSION,
        pack_id: "wikidata-facts".into(),
        pack_type: PackType::Wikidata,
        title: "Wikidata facts".into(),
        snapshot_date: snapshot.into(),
        build_date: pack::today(),
        replaces: vec![],
        license: "CC0 1.0".into(),
        attribution: "Wikidata contributors".into(),
        counts: None,
        embedder: None,
        tantivy_version: None,
        model: None,
        user_document: false,
        files,
        size_bytes: size,
    };
    std::fs::write(out.join("manifest.json"), serde_json::to_vec_pretty(&m)?)?;
    eprintln!("wikidata: {n} facts, {:.2} GB", size as f64 / 1e9);
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn build_model(
    gguf: Option<&Path>,
    dir: Option<&Path>,
    file: Option<&str>,
    out: &Path,
    pack_id: &str,
    title: &str,
    role: &str,
    repo: &str,
    rev: &str,
    license: &str,
    attribution: Option<&str>,
    n_ctx: u32,
    link: bool,
) -> Result<()> {
    std::fs::create_dir_all(out)?;
    let name = if let Some(dir) = dir {
        for e in std::fs::read_dir(dir)? {
            let e = e?;
            std::fs::copy(e.path(), out.join(e.file_name())).with_context(|| format!("copy {}", e.path().display()))?;
        }
        let name = file.context("--dir needs --file")?;
        ensure!(out.join(name).is_file(), "{name} is not in {}", dir.display());
        name.to_string()
    } else {
        let gguf = gguf.context("give --gguf or --dir")?;
        let name = gguf.file_name().context("gguf file name")?.to_string_lossy().into_owned();
        let dest = out.join(&name);
        let _ = std::fs::remove_file(&dest);
        if link {
            std::fs::hard_link(gguf, &dest)?;
        } else {
            std::fs::copy(gguf, &dest)?;
        }
        name
    };
    let role = match role {
        "llm-fast" => ModelRole::LlmFast,
        "llm-small" => ModelRole::LlmSmall,
        "stt" => ModelRole::Stt,
        _ => ModelRole::LlmDeep,
    };
    let _ = std::fs::remove_file(out.join("manifest.json"));
    let (files, size) = import::file_entries(out)?;
    let m = Manifest {
        format_version: FORMAT_VERSION,
        pack_id: pack_id.into(),
        pack_type: PackType::Model,
        title: title.into(),
        snapshot_date: String::new(),
        build_date: pack::today(),
        replaces: vec![],
        license: license.into(),
        attribution: attribution.unwrap_or(repo).into(),
        counts: None,
        embedder: None,
        tantivy_version: None,
        model: Some(ModelInfo { role, file: name, hf_repo: repo.into(), revision: rev.into(), n_ctx }),
        user_document: false,
        files,
        size_bytes: size,
    };
    std::fs::write(out.join("manifest.json"), serde_json::to_vec_pretty(&m)?)?;
    Ok(())
}

/// Tar stream split across files of at most `part_size` bytes, or one file `<stem>.tar` when `single`.
struct PartWriter {
    dir: PathBuf,
    stem: String,
    part_size: u64,
    single: bool,
    cur: Option<(File, Sha256, u64, String)>,
    parts: Vec<import::PartEntry>,
}

impl PartWriter {
    fn close(&mut self) -> std::io::Result<()> {
        if let Some((mut f, h, n, name)) = self.cur.take() {
            f.flush()?;
            self.parts.push(import::PartEntry { name, bytes: n, sha256: hex::encode(h.finalize()) });
        }
        Ok(())
    }

    fn roll(&mut self) -> std::io::Result<()> {
        self.close()?;
        let name = if self.single { format!("{}.tar", self.stem) } else { format!("{}.tar.part{:03}", self.stem, self.parts.len() + 1) };
        self.cur = Some((File::create(self.dir.join(&name))?, Sha256::new(), 0, name));
        Ok(())
    }
}

impl Write for PartWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if self.cur.as_ref().is_none_or(|c| c.2 >= self.part_size) {
            self.roll()?;
        }
        let (f, h, n, _) = self.cur.as_mut().unwrap();
        let take = buf.len().min((self.part_size - *n) as usize);
        let w = f.write(&buf[..take])?;
        h.update(&buf[..w]);
        *n += w as u64;
        Ok(w)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.cur.as_mut().map(|c| c.0.flush()).unwrap_or(Ok(()))
    }
}

/// One tar stream of the pack directories, named `stem`. A bundle has several packs; the parts of a split pack have one.
fn split(packs: &[PathBuf], stem: &str, out: &Path, part_size: u64, single: bool) -> Result<()> {
    std::fs::create_dir_all(out)?;
    let part_size = if single { u64::MAX } else { part_size };
    let mut pw = PartWriter { dir: out.to_path_buf(), stem: stem.to_string(), part_size, single, cur: None, parts: vec![] };
    {
        let mut tb = tar::Builder::new(&mut pw);
        tb.mode(tar::HeaderMode::Deterministic);
        tb.sparse(false); // plain entries: the 0.1.0 app rejects GNU sparse entries
        for pack in packs {
            tb.append_dir_all(&Manifest::read(pack)?.pack_id, pack)?;
        }
        tb.finish()?;
    }
    pw.close()?;
    for p in &pw.parts {
        eprintln!("{}  {:>12}  {}", p.sha256, p.bytes, p.name);
    }
    if !single {
        let idx = import::PartIndex { pack_id: stem.to_string(), parts: pw.parts };
        std::fs::write(out.join(format!("{stem}.pack.json")), serde_json::to_vec_pretty(&idx)?)?;
    }
    Ok(())
}

fn main() -> Result<()> {
    match Cli::parse().cmd {
        Cmd::Build { input, out, pack_id, title, snapshot, license, attribution, replaces, embedder_doc, embedder_query, zstd_level, no_sparse, notices } => {
            let embedder = input.join("dense").exists().then(|| Embedder {
                doc: embedder_doc,
                query: embedder_query,
                dims: 512,
                encoding: "binary-sign-mrl".into(),
            });
            build(&input, &out, &pack_id, &title, &snapshot, &license, &attribution, replaces, embedder, zstd_level, no_sparse)?;
            add_notices(&out, &notices, None, None)
        }
        Cmd::Meta { input, pack } => replace_meta(&input, &pack),
        Cmd::Dense { input, pack, embedder_doc, embedder_query } => {
            replace_dense(&input, &pack, Embedder { doc: embedder_doc, query: embedder_query, dims: 512, encoding: "binary-sign-mrl".into() })
        }
        Cmd::Notice { pack, notices, license, attribution } => add_notices(&pack, &notices, license.as_deref(), attribution.as_deref()),
        Cmd::Wikidata { input, out, snapshot, notices } => {
            build_wikidata(&input, &out, &snapshot)?;
            add_notices(&out, &notices, None, None)
        }
        Cmd::Model { gguf, dir, file, out, pack_id, title, role, hf_repo, revision, license, n_ctx, link, attribution, notices } => {
            build_model(gguf.as_deref(), dir.as_deref(), file.as_deref(), &out, &pack_id, &title, &role, &hf_repo, &revision, &license, attribution.as_deref(), n_ctx, link)?;
            add_notices(&out, &notices, None, None)
        }
        Cmd::Split { pack, out, part_size, single } => split(std::slice::from_ref(&pack), &Manifest::read(&pack)?.pack_id, &out, part_size, single),
        Cmd::Bundle { pack, out } => {
            ensure!(out.extension().is_some_and(|e| e == "tar"), "--out must be a .tar file");
            split(&pack, &out.file_stem().unwrap().to_string_lossy(), out.parent().unwrap_or(Path::new("")), u64::MAX, true)
        }
        Cmd::Verify { pack } => {
            let bad = import::verify(&pack)?;
            if bad.is_empty() {
                eprintln!("ok");
                Ok(())
            } else {
                bail!("mismatched files: {bad:?}")
            }
        }
    }
}
