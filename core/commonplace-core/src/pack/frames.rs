//! Frame store: records packed into zstd frames that share one trained dictionary.
//!
//! `frames.idx` layout (little endian): magic `CPFI`, u32 version, u32 records_per_frame,
//! u64 n_records, u64 n_frames, then n_frames + 1 u64 byte offsets into `frames.bin`.
//! A decompressed frame is: u32 n, (n + 1) u32 offsets, record bytes.

use anyhow::{Context, Result, bail, ensure};
use memmap2::Mmap;
use rayon::prelude::*;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

const MAGIC: &[u8; 4] = b"CPFI";
const VERSION: u32 = 1;
const HEADER: usize = 4 + 4 + 4 + 8 + 8;
pub const RECORDS_PER_FRAME: u32 = 64;

pub struct FrameStore {
    bin: Mmap,
    idx: Mmap,
    dict: zstd::dict::DecoderDictionary<'static>,
    per_frame: u32,
    n_records: u64,
    n_frames: u64,
}

impl FrameStore {
    pub fn open(dir: &Path) -> Result<Self> {
        let map = |name: &str| -> Result<Mmap> {
            let f = File::open(dir.join(name)).with_context(|| format!("open {}", dir.join(name).display()))?;
            // SAFETY: pack files are immutable once installed.
            Ok(unsafe { Mmap::map(&f)? })
        };
        let bin = map("frames.bin")?;
        let idx = map("frames.idx")?;
        let dict_bytes = std::fs::read(dir.join("dict.zstd"))?;
        ensure!(idx.len() >= HEADER && &idx[..4] == MAGIC, "bad frames.idx in {}", dir.display());
        let u32_at = |o: usize| u32::from_le_bytes(idx[o..o + 4].try_into().unwrap());
        let u64_at = |o: usize| u64::from_le_bytes(idx[o..o + 8].try_into().unwrap());
        ensure!(u32_at(4) == VERSION, "unsupported frames.idx version {}", u32_at(4));
        let per_frame = u32_at(8);
        let n_records = u64_at(12);
        let n_frames = u64_at(20);
        ensure!(idx.len() == HEADER + 8 * (n_frames as usize + 1), "truncated frames.idx");
        #[cfg(unix)]
        let _ = bin.advise(memmap2::Advice::Random);
        Ok(Self { bin, idx, dict: zstd::dict::DecoderDictionary::copy(&dict_bytes), per_frame, n_records, n_frames })
    }

    pub fn len(&self) -> u64 {
        self.n_records
    }

    pub fn is_empty(&self) -> bool {
        self.n_records == 0
    }

    fn frame_offset(&self, f: u64) -> u64 {
        let o = HEADER + 8 * f as usize;
        u64::from_le_bytes(self.idx[o..o + 8].try_into().unwrap())
    }

    /// Decompress one frame and return all its records.
    pub fn frame(&self, f: u64) -> Result<Frame> {
        ensure!(f < self.n_frames, "frame {f} out of range");
        let (a, b) = (self.frame_offset(f) as usize, self.frame_offset(f + 1) as usize);
        let src = &self.bin[a..b];
        let size = zstd::zstd_safe::get_frame_content_size(src)
            .ok()
            .flatten()
            .context("frame lacks content size")? as usize;
        let mut d = zstd::bulk::Decompressor::with_prepared_dictionary(&self.dict)?;
        let raw = d.decompress(src, size)?;
        Frame::parse(raw)
    }

    pub fn get(&self, id: u64) -> Result<Vec<u8>> {
        ensure!(id < self.n_records, "record {id} out of range ({})", self.n_records);
        let frame = self.frame(id / self.per_frame as u64)?;
        Ok(frame.record((id % self.per_frame as u64) as usize).to_vec())
    }

    /// Fetch many records, decompressing each needed frame once.
    pub fn get_many(&self, ids: &[u64]) -> Result<Vec<Vec<u8>>> {
        let mut frames: std::collections::HashMap<u64, Frame> = Default::default();
        let mut out = Vec::with_capacity(ids.len());
        for &id in ids {
            ensure!(id < self.n_records, "record {id} out of range");
            let f = id / self.per_frame as u64;
            if !frames.contains_key(&f) {
                frames.insert(f, self.frame(f)?);
            }
            out.push(frames[&f].record((id % self.per_frame as u64) as usize).to_vec());
        }
        Ok(out)
    }
}

pub struct Frame {
    raw: Vec<u8>,
    n: usize,
}

impl Frame {
    fn parse(raw: Vec<u8>) -> Result<Self> {
        ensure!(raw.len() >= 4, "short frame");
        let n = u32::from_le_bytes(raw[..4].try_into().unwrap()) as usize;
        ensure!(raw.len() >= 4 + 4 * (n + 1), "short frame header");
        Ok(Self { raw, n })
    }

    pub fn len(&self) -> usize {
        self.n
    }

    pub fn is_empty(&self) -> bool {
        self.n == 0
    }

    pub fn record(&self, i: usize) -> &[u8] {
        let off = |k: usize| u32::from_le_bytes(self.raw[4 + 4 * k..8 + 4 * k].try_into().unwrap()) as usize;
        let base = 4 + 4 * (self.n + 1);
        &self.raw[base + off(i)..base + off(i + 1)]
    }
}

fn encode_frame(records: &[Vec<u8>]) -> Vec<u8> {
    let total: usize = records.iter().map(|r| r.len()).sum();
    let mut raw = Vec::with_capacity(4 + 4 * (records.len() + 1) + total);
    raw.extend_from_slice(&(records.len() as u32).to_le_bytes());
    let mut off = 0u32;
    raw.extend_from_slice(&off.to_le_bytes());
    for r in records {
        off += r.len() as u32;
        raw.extend_from_slice(&off.to_le_bytes());
    }
    for r in records {
        raw.extend_from_slice(r);
    }
    raw
}

/// Train a dictionary from sample records (one sample per record).
pub fn train_dict(samples: &[Vec<u8>], size: usize) -> Result<Vec<u8>> {
    if samples.len() < 8 {
        bail!("need at least 8 samples to train a dictionary");
    }
    Ok(zstd::dict::from_samples(samples, size)?)
}

/// Streams records into a frame store. Frames are compressed in parallel batches.
pub struct FrameWriter {
    bin: BufWriter<File>,
    offsets: Vec<u64>,
    pending: Vec<Vec<u8>>,
    dict: Vec<u8>,
    level: i32,
    n_records: u64,
    idx_path: std::path::PathBuf,
}

const BATCH_FRAMES: usize = 2048;

impl FrameWriter {
    pub fn create(dir: &Path, dict: Vec<u8>, level: i32) -> Result<Self> {
        std::fs::create_dir_all(dir)?;
        std::fs::write(dir.join("dict.zstd"), &dict)?;
        Ok(Self {
            bin: BufWriter::new(File::create(dir.join("frames.bin"))?),
            offsets: vec![0],
            pending: Vec::new(),
            dict,
            level,
            n_records: 0,
            idx_path: dir.join("frames.idx"),
        })
    }

    pub fn push(&mut self, record: Vec<u8>) -> Result<()> {
        self.pending.push(record);
        self.n_records += 1;
        if self.pending.len() >= BATCH_FRAMES * RECORDS_PER_FRAME as usize {
            self.flush_batch()?;
        }
        Ok(())
    }

    fn flush_batch(&mut self) -> Result<()> {
        let dict = zstd::dict::EncoderDictionary::copy(&self.dict, self.level);
        let compressed: Vec<Vec<u8>> = self
            .pending
            .par_chunks(RECORDS_PER_FRAME as usize)
            .map(|chunk| {
                let mut c = zstd::bulk::Compressor::with_prepared_dictionary(&dict)?;
                c.include_contentsize(true)?;
                Ok(c.compress(&encode_frame(chunk))?)
            })
            .collect::<Result<_>>()?;
        for frame in compressed {
            self.bin.write_all(&frame)?;
            let last = *self.offsets.last().unwrap();
            self.offsets.push(last + frame.len() as u64);
        }
        self.pending.clear();
        Ok(())
    }

    pub fn finish(mut self) -> Result<u64> {
        self.flush_batch()?;
        self.bin.flush()?;
        let mut idx = BufWriter::new(File::create(&self.idx_path)?);
        idx.write_all(MAGIC)?;
        idx.write_all(&VERSION.to_le_bytes())?;
        idx.write_all(&RECORDS_PER_FRAME.to_le_bytes())?;
        idx.write_all(&self.n_records.to_le_bytes())?;
        idx.write_all(&((self.offsets.len() - 1) as u64).to_le_bytes())?;
        for o in &self.offsets {
            idx.write_all(&o.to_le_bytes())?;
        }
        idx.flush()?;
        Ok(self.n_records)
    }
}
