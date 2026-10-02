//! Binary IVF index over sign-binarized MRL embeddings.
//!
//! Bit packing matches `numpy.packbits` (big-endian within a byte): dim `d` is bit
//! `0x80 >> (d % 8)` of byte `d / 8`. Files: `info.json`, `centroids.f16` [n_lists × dims],
//! `lists.idx` (n_lists + 1 u64 code offsets), `codes.bin` [n × dims/8], `ids.bin` [n × u32].

use anyhow::{Context, Result, ensure};
use half::f16;
use half::slice::HalfFloatSliceExt;
use memmap2::Mmap;
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DenseInfo {
    pub dims: u32,
    pub n_lists: u32,
    pub n_codes: u64,
}

pub struct DenseIndex {
    pub info: DenseInfo,
    centroids: Mmap,
    lists: Mmap,
    codes: Mmap,
    ids: Mmap,
}

fn map(p: &Path) -> Result<Mmap> {
    let f = File::open(p).with_context(|| format!("open {}", p.display()))?;
    // SAFETY: pack files are immutable once installed.
    Ok(unsafe { Mmap::map(&f)? })
}

pub fn pack_bits(v: &[f32]) -> Vec<u8> {
    let mut out = vec![0u8; v.len().div_ceil(8)];
    for (d, &x) in v.iter().enumerate() {
        if x > 0.0 {
            out[d / 8] |= 0x80 >> (d % 8);
        }
    }
    out
}

fn hamming(a: &[u8], b: &[u8]) -> u32 {
    a.chunks_exact(8)
        .zip(b.chunks_exact(8))
        .map(|(x, y)| (u64::from_le_bytes(x.try_into().unwrap()) ^ u64::from_le_bytes(y.try_into().unwrap())).count_ones())
        .sum()
}

/// Float query · ±1 code, computed as 2·Σ(q over set bits) − Σq.
fn asym_score(q: &[f32], q_sum: f32, code: &[u8]) -> f32 {
    let mut pos = 0.0f32;
    for (j, &byte) in code.iter().enumerate() {
        let mut b = byte;
        while b != 0 {
            let lz = b.leading_zeros() as usize;
            pos += q[j * 8 + lz];
            b &= !(0x80 >> lz);
        }
    }
    2.0 * pos - q_sum
}

impl DenseIndex {
    pub fn open(dir: &Path) -> Result<Self> {
        let info: DenseInfo = serde_json::from_slice(&std::fs::read(dir.join("info.json"))?)?;
        let s = Self {
            centroids: map(&dir.join("centroids.f16"))?,
            lists: map(&dir.join("lists.idx"))?,
            codes: map(&dir.join("codes.bin"))?,
            ids: map(&dir.join("ids.bin"))?,
            info,
        };
        let (d, nl, n) = (s.info.dims as usize, s.info.n_lists as usize, s.info.n_codes as usize);
        ensure!(d % 64 == 0, "dims must be a multiple of 64");
        ensure!(s.centroids.len() == nl * d * 2, "centroids.f16 size mismatch");
        ensure!(s.lists.len() == (nl + 1) * 8, "lists.idx size mismatch");
        ensure!(s.codes.len() == n * d / 8, "codes.bin size mismatch");
        ensure!(s.ids.len() == n * 4, "ids.bin size mismatch");
        Ok(s)
    }

    fn list_range(&self, l: usize) -> (usize, usize) {
        let at = |i: usize| u64::from_le_bytes(self.lists[i * 8..i * 8 + 8].try_into().unwrap()) as usize;
        (at(l), at(l + 1))
    }

    fn centroids_f16(&self) -> &[f16] {
        // SAFETY: the mmap is page aligned and its length was checked in open().
        unsafe { std::slice::from_raw_parts(self.centroids.as_ptr() as *const f16, self.centroids.len() / 2) }
    }

    /// `query` must be the L2-normalized first `dims` dims of the query embedding.
    /// Returns up to `k` (passage_id, score) pairs, best first.
    pub fn search(&self, query: &[f32], nprobe: usize, hamming_k: usize, k: usize) -> Vec<(u32, f32)> {
        let d = self.info.dims as usize;
        let nl = self.info.n_lists as usize;
        let q = &query[..d];
        let cents = self.centroids_f16();
        let mut buf = vec![0f32; d];
        let mut list_scores: Vec<(f32, usize)> = (0..nl)
            .map(|l| {
                cents[l * d..(l + 1) * d].convert_to_f32_slice(&mut buf);
                (buf.iter().zip(q).map(|(a, b)| a * b).sum::<f32>(), l)
            })
            .collect();
        let nprobe = nprobe.min(nl);
        if nprobe < nl {
            list_scores.select_nth_unstable_by(nprobe, |a, b| b.0.total_cmp(&a.0));
        }

        let q_bits = pack_bits(q);
        let cb = d / 8;
        let mut cands: Vec<(u32, u32)> = Vec::new(); // (hamming, code index)
        for &(_, l) in &list_scores[..nprobe] {
            let (a, b) = self.list_range(l);
            #[cfg(unix)]
            let _ = self.codes.advise_range(memmap2::Advice::Sequential, a * cb, (b - a) * cb);
            for i in a..b {
                cands.push((hamming(&q_bits, &self.codes[i * cb..(i + 1) * cb]), i as u32));
            }
        }
        if cands.len() > hamming_k {
            cands.select_nth_unstable_by_key(hamming_k, |c| c.0);
            cands.truncate(hamming_k);
        }
        let q_sum: f32 = q.iter().sum();
        let mut scored: Vec<(u32, f32)> = cands
            .iter()
            .map(|&(_, i)| {
                let i = i as usize;
                let id = u32::from_le_bytes(self.ids[i * 4..i * 4 + 4].try_into().unwrap());
                (id, asym_score(q, q_sum, &self.codes[i * cb..(i + 1) * cb]))
            })
            .collect();
        scored.sort_unstable_by(|a, b| b.1.total_cmp(&a.1));
        scored.truncate(k);
        scored
    }
}

/// Write a dense index. `codes` is n × dims/8 bytes; `assign[i]` is the list of code i and `ids[i]` its
/// passage id (`None`: code i is passage i).
pub fn write(dir: &Path, dims: u32, centroids: &[f32], codes: &[u8], assign: &[u32], ids: Option<&[u32]>) -> Result<DenseInfo> {
    let d = dims as usize;
    let cb = d / 8;
    let n = assign.len();
    let nl = centroids.len() / d;
    ensure!(codes.len() == n * cb, "codes length {} != n*{}", codes.len(), cb);
    ensure!(ids.is_none_or(|v| v.len() == n), "ids length mismatch");
    std::fs::create_dir_all(dir)?;

    let c16: Vec<f16> = centroids.iter().map(|&x| f16::from_f32(x)).collect();
    let mut w = BufWriter::new(File::create(dir.join("centroids.f16"))?);
    for x in &c16 {
        w.write_all(&x.to_le_bytes())?;
    }
    w.flush()?;

    let mut counts = vec![0u64; nl + 1];
    for &a in assign {
        counts[a as usize + 1] += 1;
    }
    for l in 0..nl {
        counts[l + 1] += counts[l];
    }
    let mut w = BufWriter::new(File::create(dir.join("lists.idx"))?);
    for c in &counts {
        w.write_all(&c.to_le_bytes())?;
    }
    w.flush()?;

    // Counting sort into list order.
    let mut order = vec![0u32; n];
    let mut next = counts.clone();
    for (i, &a) in assign.iter().enumerate() {
        order[next[a as usize] as usize] = i as u32;
        next[a as usize] += 1;
    }
    let mut wc = BufWriter::with_capacity(1 << 22, File::create(dir.join("codes.bin"))?);
    let mut wi = BufWriter::with_capacity(1 << 20, File::create(dir.join("ids.bin"))?);
    for &i in &order {
        let i = i as usize;
        wc.write_all(&codes[i * cb..(i + 1) * cb])?;
        wi.write_all(&ids.map_or(i as u32, |v| v[i]).to_le_bytes())?;
    }
    wc.flush()?;
    wi.flush()?;

    let info = DenseInfo { dims, n_lists: nl as u32, n_codes: n as u64 };
    std::fs::write(dir.join("info.json"), serde_json::to_vec_pretty(&info)?)?;
    Ok(info)
}
