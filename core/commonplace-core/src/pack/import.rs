//! Streaming pack import: the parts of one tar stream are read in order, each file is hashed while
//! it is extracted, and the result is checked against `manifest.json` before it is moved into place.

use super::{Manifest, dir_size};
use anyhow::{Context, Result, bail, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

/// Distribution index published next to the parts (`<pack_id>.pack.json`).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PartIndex {
    pub pack_id: String,
    pub parts: Vec<PartEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PartEntry {
    pub name: String,
    pub bytes: u64,
    pub sha256: String,
}

pub trait ImportProgress {
    fn bytes(&mut self, done: u64);
    /// Called when a part has been fully read and its hash checked.
    fn part_done(&mut self, index: usize);
}

struct PartsReader<'a, P: ImportProgress> {
    parts: Vec<Box<dyn Read + Send + 'a>>,
    expected: Vec<Option<String>>,
    cur: usize,
    hasher: Sha256,
    done: u64,
    progress: &'a mut P,
}

impl<P: ImportProgress> Read for PartsReader<'_, P> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        while self.cur < self.parts.len() {
            let n = self.parts[self.cur].read(buf)?;
            if n > 0 {
                self.hasher.update(&buf[..n]);
                self.done += n as u64;
                self.progress.bytes(self.done);
                return Ok(n);
            }
            let got = hex::encode(std::mem::take(&mut self.hasher).finalize());
            if let Some(want) = &self.expected[self.cur]
                && *want != got
            {
                return Err(std::io::Error::other(format!("part {} sha256 mismatch", self.cur + 1)));
            }
            self.progress.part_done(self.cur);
            self.cur += 1;
        }
        Ok(0)
    }
}

struct HashWriter<W: Write> {
    inner: W,
    h: Sha256,
    n: u64,
}

impl<W: Write> Write for HashWriter<W> {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        let n = self.inner.write(b)?;
        self.h.update(&b[..n]);
        self.n += n as u64;
        Ok(n)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        self.inner.flush()
    }
}

fn safe_rel(p: &Path) -> Result<PathBuf> {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            Component::Normal(s) => out.push(s),
            Component::CurDir => {}
            _ => bail!("unsafe path in archive: {}", p.display()),
        }
    }
    Ok(out)
}

/// Import a pack from its parts, in order. `packs_dir` is the library's pack directory.
/// Returns the installed manifest. The tar stream holds a single top-level `<pack_id>/` directory.
pub fn import<P: ImportProgress>(
    parts: Vec<Box<dyn Read + Send + '_>>,
    expected_part_hashes: Vec<Option<String>>,
    packs_dir: &Path,
    progress: &mut P,
    validate: &dyn Fn(&Manifest) -> Result<()>,
) -> Result<Manifest> {
    ensure!(parts.len() == expected_part_hashes.len(), "hash list length mismatch");
    let staging = packs_dir.join(".staging");
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging)?;

    let reader = PartsReader { parts, expected: expected_part_hashes, cur: 0, hasher: Sha256::new(), done: 0, progress };
    let mut ar = tar::Archive::new(reader);
    let mut hashes: HashMap<String, (u64, String)> = HashMap::new();
    let mut root: Option<String> = None;
    for entry in ar.entries()? {
        let mut entry = entry?;
        let rel = safe_rel(&entry.path()?)?;
        let mut comps = rel.components();
        let Some(top) = comps.next() else { continue };
        let top = top.as_os_str().to_string_lossy().into_owned();
        match &root {
            None => root = Some(top.clone()),
            Some(r) => ensure!(*r == top, "archive has more than one top-level directory"),
        }
        let inner: PathBuf = comps.collect();
        let dest = staging.join(&top).join(&inner);
        match entry.header().entry_type() {
            tar::EntryType::Directory => std::fs::create_dir_all(&dest)?,
            tar::EntryType::Regular => {
                if let Some(p) = dest.parent() {
                    std::fs::create_dir_all(p)?;
                }
                let f = std::fs::File::create(&dest).with_context(|| format!("create {}", dest.display()))?;
                let mut w = HashWriter { inner: std::io::BufWriter::with_capacity(1 << 20, f), h: Sha256::new(), n: 0 };
                std::io::copy(&mut entry, &mut w)?;
                w.flush()?;
                let key = inner.to_string_lossy().replace('\\', "/");
                hashes.insert(key, (w.n, hex::encode(w.h.finalize())));
            }
            other => bail!("unsupported tar entry type {other:?}"),
        }
    }
    // Drain the reader so the final part's hash is checked.
    std::io::copy(ar.into_inner().by_ref(), &mut std::io::sink())?;

    let root = root.context("empty archive")?;
    let src = staging.join(&root);
    let manifest = Manifest::read(&src)?;
    ensure!(manifest.pack_id == root, "manifest pack_id {} != directory {}", manifest.pack_id, root);
    validate(&manifest)?;
    for f in &manifest.files {
        let (n, h) = hashes.get(&f.path).with_context(|| format!("missing file {}", f.path))?;
        ensure!(*n == f.bytes && *h == f.sha256, "{}: size or sha256 mismatch", f.path);
    }
    let dest = packs_dir.join(&root);
    if dest.exists() {
        let old = packs_dir.join(format!(".old-{root}"));
        let _ = std::fs::remove_dir_all(&old);
        std::fs::rename(&dest, &old)?;
        std::fs::rename(&src, &dest)?;
        let _ = std::fs::remove_dir_all(&old);
    } else {
        std::fs::rename(&src, &dest)?;
    }
    let _ = std::fs::remove_dir_all(&staging);
    Ok(manifest)
}

/// Re-hash every file of an installed pack against its manifest.
pub fn verify(dir: &Path) -> Result<Vec<String>> {
    let m = Manifest::read(dir)?;
    let mut bad = Vec::new();
    for f in &m.files {
        let p = dir.join(&f.path);
        let ok = std::fs::metadata(&p).map(|md| md.len() == f.bytes).unwrap_or(false)
            && super::sha256_file(&p).map(|h| h == f.sha256).unwrap_or(false);
        if !ok {
            bad.push(f.path.clone());
        }
    }
    Ok(bad)
}

/// Build the file list for a manifest (every file except manifest.json itself).
pub fn file_entries(dir: &Path) -> Result<(Vec<super::FileEntry>, u64)> {
    fn walk(base: &Path, p: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
        for e in std::fs::read_dir(p)? {
            let e = e?;
            if e.file_type()?.is_dir() {
                walk(base, &e.path(), out)?;
            } else {
                out.push(e.path().strip_prefix(base)?.to_path_buf());
            }
        }
        Ok(())
    }
    let mut files = Vec::new();
    walk(dir, dir, &mut files)?;
    files.sort();
    let entries: Vec<super::FileEntry> = files
        .iter()
        .filter(|p| p.as_os_str() != "manifest.json")
        .map(|p| {
            let full = dir.join(p);
            Ok(super::FileEntry {
                path: p.to_string_lossy().replace('\\', "/"),
                bytes: std::fs::metadata(&full)?.len(),
                sha256: super::sha256_file(&full)?,
            })
        })
        .collect::<Result<_>>()?;
    Ok((entries, dir_size(dir)))
}
