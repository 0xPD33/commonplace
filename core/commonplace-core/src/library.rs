//! The installed library: `<root>/packs/<pack_id>/` for knowledge, Wikidata and model packs.

use crate::pack::{self, Embedder, Manifest, ModelRole, Pack, PackType};
use crate::tools::wikidata::WikidataDb;
use anyhow::{Result, bail};
use std::path::{Path, PathBuf};

/// Bounty cap on the total footprint (app + models + packs).
pub const FOOTPRINT_CAP: u64 = 50_000_000_000;

pub struct ModelPack {
    pub dir: PathBuf,
    pub manifest: Manifest,
}

impl ModelPack {
    pub fn role(&self) -> Option<&ModelRole> {
        self.manifest.model.as_ref().map(|m| &m.role)
    }
    pub fn file(&self) -> Option<PathBuf> {
        self.manifest.model.as_ref().map(|m| self.dir.join(&m.file))
    }
}

pub struct Library {
    pub root: PathBuf,
    pub packs: Vec<Pack>,
    pub wikidata: Option<(Manifest, WikidataDb)>,
    pub models: Vec<ModelPack>,
    pub embedder: Option<Embedder>,
    /// Packs that were found but not loaded, with the reason.
    pub skipped: Vec<(String, String)>,
}

impl Library {
    pub fn packs_dir(root: &Path) -> PathBuf {
        root.join("packs")
    }

    pub fn open(root: &Path) -> Result<Self> {
        let dir = Self::packs_dir(root);
        std::fs::create_dir_all(&dir)?;
        let mut entries: Vec<PathBuf> = std::fs::read_dir(&dir)?
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir() && !p.file_name().is_some_and(|n| n.to_string_lossy().starts_with('.')))
            .collect();
        entries.sort();
        let manifests: Vec<(PathBuf, Result<Manifest>)> = entries.into_iter().map(|p| (p.clone(), Manifest::read(&p))).collect();
        let replaced: Vec<String> =
            manifests.iter().filter_map(|(_, m)| m.as_ref().ok()).flat_map(|m| m.replaces.clone()).collect();

        let mut lib = Library { root: root.to_path_buf(), packs: vec![], wikidata: None, models: vec![], embedder: None, skipped: vec![] };
        for (path, m) in manifests {
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            let m = match m {
                Ok(m) => m,
                Err(e) => {
                    lib.skipped.push((name, format!("{e:#}")));
                    continue;
                }
            };
            if replaced.contains(&m.pack_id) {
                lib.skipped.push((m.pack_id.clone(), "replaced by a larger pack".into()));
                continue;
            }
            match m.pack_type {
                PackType::Model => lib.models.push(ModelPack { dir: path, manifest: m }),
                PackType::Wikidata => match WikidataDb::open(&path.join("wikidata.sqlite")) {
                    Ok(db) => lib.wikidata = Some((m, db)),
                    Err(e) => lib.skipped.push((m.pack_id.clone(), format!("{e:#}"))),
                },
                PackType::Knowledge => {
                    if let (Some(have), Some(e)) = (&lib.embedder, &m.embedder)
                        && have != e
                    {
                        lib.skipped.push((m.pack_id.clone(), "embedder differs from the installed packs".into()));
                        continue;
                    }
                    if lib.packs.len() >= u8::MAX as usize {
                        lib.skipped.push((m.pack_id.clone(), "too many packs".into()));
                        continue;
                    }
                    match Pack::open(&path) {
                        Ok(p) => {
                            if lib.embedder.is_none() {
                                lib.embedder = p.manifest.embedder.clone();
                            }
                            lib.packs.push(p);
                        }
                        Err(e) => lib.skipped.push((m.pack_id.clone(), format!("{e:#}"))),
                    }
                }
            }
        }
        Ok(lib)
    }

    /// Refuse a knowledge pack whose embedder differs from the installed ones.
    pub fn check_compatible(&self, m: &Manifest) -> Result<()> {
        if m.pack_type == PackType::Knowledge
            && let (Some(have), Some(e)) = (&self.embedder, &m.embedder)
            && have != e
            && !self.packs.iter().all(|p| m.replaces.contains(&p.manifest.pack_id))
        {
            bail!("pack {} uses embedder {} but installed packs use {}", m.pack_id, e.doc, have.doc);
        }
        Ok(())
    }

    /// The GGUF model pack with `role`. LiteRT packs (`.litertlm`) belong to the Kotlin backend.
    pub fn model(&self, role: &ModelRole) -> Option<&ModelPack> {
        self.models.iter().find(|m| m.role() == Some(role) && !m.file().is_some_and(|f| f.extension().is_some_and(|e| e == "litertlm")))
    }

    pub fn total_bytes(&self) -> u64 {
        pack::dir_size(&Self::packs_dir(&self.root))
    }

    /// Snapshot date of the largest knowledge pack.
    pub fn snapshot_date(&self) -> String {
        self.packs
            .iter()
            .max_by_key(|p| p.manifest.counts.as_ref().map(|c| c.passages).unwrap_or(0))
            .map(|p| p.manifest.snapshot_date.clone())
            .unwrap_or_else(|| "unknown".into())
    }
}
