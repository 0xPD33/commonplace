//! Streaming pack import: the parts of one tar stream are read in order, each file is hashed while
//! it is extracted, and the result is checked against `manifest.json` before it is moved into place.
//! A stream (a bundle) can hold several packs, one top-level `<pack_id>/` directory after the other.

use super::{Manifest, dir_size, installed_size};
use anyhow::{Context, Result, anyhow, bail, ensure};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::cell::RefCell;
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

/// What an import may do with the stream it reads.
pub struct ImportRules<'a> {
    /// The app's catalog holds the SHA-256 of every part. Only such a stream may replace an installed pack.
    pub trusted: bool,
    /// The one pack that an untrusted stream may hold: the id in its file name.
    pub only_pack: Option<&'a str>,
    /// The library must stay at or below this many bytes.
    pub cap: u64,
}

/// An empty file in a pack that came from outside the catalog. The Library shows it.
pub const UNVERIFIED_MARK: &str = ".unverified";

const ENOSPC: i32 = 28;

pub trait ImportProgress {
    fn bytes(&mut self, done: u64);
    /// Called when a part has been fully read and its hash checked.
    fn part_done(&mut self, index: usize);
    /// Called when the stream starts a pack.
    fn pack_started(&mut self, pack_id: &str);
}

struct PartsReader<'a, P: ImportProgress> {
    parts: Vec<Box<dyn Read + Send + 'a>>,
    expected: Vec<Option<String>>,
    cur: usize,
    hasher: Sha256,
    done: u64,
    progress: &'a RefCell<&'a mut P>,
}

impl<P: ImportProgress> Read for PartsReader<'_, P> {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        while self.cur < self.parts.len() {
            let n = self.parts[self.cur].read(buf)?;
            if n > 0 {
                self.hasher.update(&buf[..n]);
                self.done += n as u64;
                self.progress.borrow_mut().bytes(self.done);
                return Ok(n);
            }
            let got = hex::encode(std::mem::take(&mut self.hasher).finalize());
            if let Some(want) = &self.expected[self.cur]
                && *want != got
            {
                return Err(std::io::Error::other(format!("part {} sha256 mismatch", self.cur + 1)));
            }
            self.progress.borrow_mut().part_done(self.cur);
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

/// The size and SHA-256 of each extracted file, by its path in the pack.
type FileHashes = HashMap<String, (u64, String)>;

/// Check the extracted pack `id` in `staging` against its manifest.
fn check_pack(staging: &Path, id: &str, hashes: &FileHashes, validate: &dyn Fn(&Manifest) -> Result<()>) -> Result<Manifest> {
    let manifest = Manifest::read(&staging.join(id))?;
    ensure!(manifest.pack_id == id, "manifest pack_id {} != directory {}", manifest.pack_id, id);
    validate(&manifest)?;
    for f in &manifest.files {
        let (n, h) = hashes.get(&f.path).with_context(|| format!("missing file {}", f.path))?;
        ensure!(*n == f.bytes && *h == f.sha256, "{}: size or sha256 mismatch", f.path);
    }
    Ok(manifest)
}

/// Move the checked pack `id` from `staging` into place. A process killed between the two renames leaves
/// `.old-<id>`, which `Library::sweep` puts back.
fn move_into_place(staging: &Path, packs_dir: &Path, id: &str) -> Result<()> {
    let src = staging.join(id);
    let dest = packs_dir.join(id);
    if dest.exists() {
        let old = packs_dir.join(format!(".old-{id}"));
        let _ = std::fs::remove_dir_all(&old);
        std::fs::rename(&dest, &old)?;
        if let Err(e) = std::fs::rename(&src, &dest) {
            let _ = std::fs::rename(&old, &dest);
            return Err(e.into());
        }
        let _ = std::fs::remove_dir_all(&old);
    } else {
        std::fs::rename(&src, &dest)?;
    }
    Ok(())
}

/// Import the packs of a tar stream from its parts, in order. `packs_dir` is the library's pack directory.
/// The stream holds one top-level `<pack_id>/` directory per pack. Returns the installed manifests.
/// Packs move into place only after the whole stream is read and hashed, so a file that fails its hash
/// installs nothing. An error also removes the partial files.
pub fn import<P: ImportProgress>(
    parts: Vec<Box<dyn Read + Send + '_>>,
    expected_part_hashes: Vec<Option<String>>,
    packs_dir: &Path,
    rules: &ImportRules,
    progress: &mut P,
    validate: &dyn Fn(&Manifest) -> Result<()>,
) -> Result<Vec<Manifest>> {
    ensure!(parts.len() == expected_part_hashes.len(), "hash list length mismatch");
    let staging = packs_dir.join(".staging");
    let _ = std::fs::remove_dir_all(&staging);
    let result = extract_and_install(parts, expected_part_hashes, packs_dir, &staging, rules, progress, validate);
    let _ = std::fs::remove_dir_all(&staging);
    result.map_err(|e| {
        if e.chain().any(|c| c.downcast_ref::<std::io::Error>().is_some_and(|io| io.raw_os_error() == Some(ENOSPC))) {
            anyhow!("The phone ran out of storage. Nothing new was installed, and the partial files are gone. Free up space and install again.")
        } else {
            e
        }
    })
}

fn extract_and_install<P: ImportProgress>(
    parts: Vec<Box<dyn Read + Send + '_>>,
    expected: Vec<Option<String>>,
    packs_dir: &Path,
    staging: &Path,
    rules: &ImportRules,
    progress: &mut P,
    validate: &dyn Fn(&Manifest) -> Result<()>,
) -> Result<Vec<Manifest>> {
    std::fs::create_dir_all(staging)?;
    let progress = RefCell::new(progress);
    let reader = PartsReader { parts, expected, cur: 0, hasher: Sha256::new(), done: 0, progress: &progress };
    let mut ar = tar::Archive::new(reader);
    let mut packs: Vec<(String, FileHashes)> = Vec::new();
    // The size of the installed packs that this stream does not replace, and of what it has extracted so far.
    let (mut others, mut extracted) = (0u64, 0u64);
    for entry in ar.entries()? {
        let mut entry = entry?;
        let rel = safe_rel(&entry.path()?)?;
        let mut comps = rel.components();
        let Some(top) = comps.next() else { continue };
        let top = top.as_os_str().to_string_lossy().into_owned();
        if packs.last().is_none_or(|p| p.0 != top) {
            ensure!(!top.starts_with('.') && !packs.iter().any(|p| p.0 == top), "unexpected pack directory {top}");
            if let Some(want) = rules.only_pack {
                ensure!(top == want, "this file holds the pack {top}, not {want}");
            }
            ensure!(
                rules.trusted || !packs_dir.join(&top).exists(),
                "{top} is installed, and this file is not in the app's catalog, so the app cannot check it. Remove the pack first if you trust the file."
            );
            progress.borrow_mut().pack_started(&top);
            packs.push((top.clone(), HashMap::new()));
            others = installed_size(packs_dir, &packs.iter().map(|p| p.0.clone()).collect::<Vec<_>>());
        }
        // The real size: a small tar can declare a huge sparse file, and reading fills its holes with zeros.
        extracted = extracted.saturating_add(entry.size());
        ensure!(others.saturating_add(extracted) <= rules.cap, "this file would take the library over the {:.0} GB limit", rules.cap as f64 / 1e9);
        let inner: PathBuf = comps.collect();
        let dest = staging.join(&top).join(&inner);
        match entry.header().entry_type() {
            tar::EntryType::Directory => std::fs::create_dir_all(&dest)?,
            // Published packs hold GNU sparse entries (tar 0.4.46 writes them by default); reading fills the holes.
            tar::EntryType::Regular | tar::EntryType::GNUSparse => {
                if let Some(p) = dest.parent() {
                    std::fs::create_dir_all(p)?;
                }
                let f = std::fs::File::create(&dest).with_context(|| format!("create {}", dest.display()))?;
                let mut w = HashWriter { inner: std::io::BufWriter::with_capacity(1 << 20, f), h: Sha256::new(), n: 0 };
                std::io::copy(&mut entry, &mut w)?;
                w.flush()?;
                let key = inner.to_string_lossy().replace('\\', "/");
                let hashes = &mut packs.last_mut().context("no pack")?.1;
                hashes.insert(key, (w.n, hex::encode(w.h.finalize())));
            }
            other => bail!("unsupported tar entry type {other:?}"),
        }
    }
    // Drain the reader so the final part's hash is checked.
    std::io::copy(ar.into_inner().by_ref(), &mut std::io::sink())?;

    ensure!(!packs.is_empty(), "empty archive");
    let manifests = packs.iter().map(|(id, hashes)| check_pack(staging, id, hashes, validate).with_context(|| id.clone())).collect::<Result<Vec<_>>>()?;
    for (id, _) in &packs {
        move_into_place(staging, packs_dir, id).with_context(|| id.clone())?;
        if !rules.trusted {
            let _ = std::fs::write(packs_dir.join(id).join(UNVERIFIED_MARK), b"");
        }
    }
    Ok(manifests)
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
