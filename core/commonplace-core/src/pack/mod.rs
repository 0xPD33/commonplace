//! Pack format v1 (see docs/PACKS.md). Everything the phone reads is defined here, and
//! `packbuild` writes it through the same code.

pub mod dense;
pub mod frames;
pub mod import;
pub mod meta;
pub mod sparse;

use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

pub const FORMAT_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum PackType {
    Knowledge,
    Wikidata,
    Model,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Embedder {
    pub doc: String,
    pub query: String,
    pub dims: u32,
    pub encoding: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FileEntry {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum ModelRole {
    LlmFast,
    LlmSmall,
    LlmDeep,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    pub role: ModelRole,
    pub file: String,
    pub hf_repo: String,
    pub revision: String,
    #[serde(default = "default_ctx")]
    pub n_ctx: u32,
}

fn default_ctx() -> u32 {
    4096
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Counts {
    #[serde(default)]
    pub articles: u64,
    #[serde(default)]
    pub passages: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Manifest {
    pub format_version: u32,
    pub pack_id: String,
    #[serde(default = "default_type")]
    pub pack_type: PackType,
    pub title: String,
    pub snapshot_date: String,
    pub build_date: String,
    #[serde(default)]
    pub replaces: Vec<String>,
    pub license: String,
    pub attribution: String,
    #[serde(default)]
    pub counts: Option<Counts>,
    #[serde(default)]
    pub embedder: Option<Embedder>,
    #[serde(default)]
    pub tantivy_version: Option<String>,
    #[serde(default)]
    pub model: Option<ModelInfo>,
    pub files: Vec<FileEntry>,
    pub size_bytes: u64,
}

fn default_type() -> PackType {
    PackType::Knowledge
}

impl Manifest {
    pub fn read(dir: &Path) -> Result<Self> {
        let p = dir.join("manifest.json");
        let m: Manifest = serde_json::from_slice(&std::fs::read(&p).with_context(|| format!("read {}", p.display()))?)
            .with_context(|| format!("parse {}", p.display()))?;
        ensure!(m.format_version == FORMAT_VERSION, "{}: format_version {} unsupported", m.pack_id, m.format_version);
        Ok(m)
    }
}

/// A passage as stored in `store/`: article id, ordinal, section path, text.
#[derive(Debug, Clone, PartialEq)]
pub struct PassageRecord {
    pub article_id: u32,
    pub ordinal: u16,
    pub section_path: String,
    pub text: String,
}

impl PassageRecord {
    pub fn encode(&self) -> Vec<u8> {
        let sec = self.section_path.as_bytes();
        let sec_len = sec.len().min(u16::MAX as usize);
        let mut v = Vec::with_capacity(8 + sec_len + self.text.len());
        v.extend_from_slice(&self.article_id.to_le_bytes());
        v.extend_from_slice(&self.ordinal.to_le_bytes());
        v.extend_from_slice(&(sec_len as u16).to_le_bytes());
        v.extend_from_slice(&sec[..sec_len]);
        v.extend_from_slice(self.text.as_bytes());
        v
    }

    pub fn decode(b: &[u8]) -> Result<Self> {
        ensure!(b.len() >= 8, "short passage record");
        let sec_len = u16::from_le_bytes([b[6], b[7]]) as usize;
        ensure!(b.len() >= 8 + sec_len, "short passage record");
        Ok(Self {
            article_id: u32::from_le_bytes(b[0..4].try_into().unwrap()),
            ordinal: u16::from_le_bytes([b[4], b[5]]),
            section_path: String::from_utf8_lossy(&b[8..8 + sec_len]).into_owned(),
            text: String::from_utf8_lossy(&b[8 + sec_len..]).into_owned(),
        })
    }
}

/// One fact-card line: fact text plus the passages it came from.
#[derive(Debug, Clone, PartialEq)]
pub struct Fact {
    pub text: String,
    pub passage_ids: Vec<u32>,
}

/// Cards are stored per passage: one line per fact, `text\tid,id`.
pub fn encode_facts(facts: &[Fact]) -> Vec<u8> {
    let mut s = String::new();
    for f in facts {
        let ids: Vec<String> = f.passage_ids.iter().map(|i| i.to_string()).collect();
        s.push_str(&f.text.replace(['\t', '\n'], " "));
        s.push('\t');
        s.push_str(&ids.join(","));
        s.push('\n');
    }
    s.into_bytes()
}

pub fn decode_facts(b: &[u8]) -> Vec<Fact> {
    String::from_utf8_lossy(b)
        .lines()
        .filter_map(|l| {
            let (t, ids) = l.split_once('\t')?;
            Some(Fact { text: t.to_string(), passage_ids: ids.split(',').filter_map(|x| x.parse().ok()).collect() })
        })
        .collect()
}

/// Title normalization shared by the writer and entity linking.
pub fn normalize_title(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut space = false;
    for c in s.chars() {
        let c = if c == '_' { ' ' } else { c };
        if c.is_whitespace() {
            space = !out.is_empty();
            continue;
        }
        if space {
            out.push(' ');
            space = false;
        }
        out.extend(c.to_lowercase());
    }
    out
}

/// The text that is embedded and indexed for a passage.
pub fn index_text(title: &str, section_path: &str, text: &str) -> String {
    if section_path.is_empty() {
        format!("{title}\n{text}")
    } else {
        format!("{title} — {section_path}\n{text}")
    }
}

/// An opened knowledge pack.
pub struct Pack {
    pub dir: PathBuf,
    pub manifest: Manifest,
    pub meta: meta::MetaDb,
    pub store: frames::FrameStore,
    pub cards: Option<frames::FrameStore>,
    pub sparse: Option<sparse::SparseIndex>,
    pub dense: Option<dense::DenseIndex>,
}

impl Pack {
    pub fn open(dir: &Path) -> Result<Self> {
        let manifest = Manifest::read(dir)?;
        if manifest.pack_type != PackType::Knowledge {
            bail!("{} is not a knowledge pack", manifest.pack_id);
        }
        let opt = |sub: &str| dir.join(sub).exists();
        Ok(Self {
            meta: meta::MetaDb::open(&dir.join("meta.sqlite"))?,
            store: frames::FrameStore::open(&dir.join("store"))?,
            cards: if opt("cards") { Some(frames::FrameStore::open(&dir.join("cards"))?) } else { None },
            sparse: if opt("tantivy") { Some(sparse::SparseIndex::open(&dir.join("tantivy"))?) } else { None },
            dense: if opt("dense") { Some(dense::DenseIndex::open(&dir.join("dense"))?) } else { None },
            dir: dir.to_path_buf(),
            manifest,
        })
    }

    pub fn passage(&self, id: u32) -> Result<PassageRecord> {
        PassageRecord::decode(&self.store.get(id as u64)?)
    }

    pub fn passages(&self, ids: &[u32]) -> Result<Vec<PassageRecord>> {
        let ids: Vec<u64> = ids.iter().map(|&i| i as u64).collect();
        self.store.get_many(&ids)?.iter().map(|b| PassageRecord::decode(b)).collect()
    }

    pub fn facts(&self, id: u32) -> Result<Vec<Fact>> {
        match &self.cards {
            Some(c) if (id as u64) < c.len() => Ok(decode_facts(&c.get(id as u64)?)),
            _ => Ok(Vec::new()),
        }
    }
}

/// Sum of file sizes under a directory.
pub fn dir_size(p: &Path) -> u64 {
    let Ok(rd) = std::fs::read_dir(p) else { return 0 };
    rd.flatten()
        .map(|e| match e.file_type() {
            Ok(t) if t.is_dir() => dir_size(&e.path()),
            Ok(_) => e.metadata().map(|m| m.len()).unwrap_or(0),
            Err(_) => 0,
        })
        .sum()
}

pub fn sha256_file(p: &Path) -> Result<String> {
    use sha2::{Digest, Sha256};
    let mut f = std::fs::File::open(p)?;
    let mut h = Sha256::new();
    std::io::copy(&mut f, &mut h)?;
    Ok(hex::encode(h.finalize()))
}
