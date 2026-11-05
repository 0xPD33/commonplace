//! Learned fusion: a small scorer (linear plus one tanh layer) over rank and score features of the keyword
//! and semantic lists. It replaces RRF before the cut to the rerank pool. `pipeline/commonplace_pipeline/fusion.py`
//! trains it.

use crate::library::Library;
use crate::retrieval::Hit;
use crate::route::Entity;
use std::collections::HashMap;

/// Training and inference see the candidates within this depth of a list's top (`Retriever::near_top`).
pub const DEPTH: usize = 150;

/// One ranked list per pack: (pack, [(passage id, score)]), best first.
pub type Lists = [(u8, Vec<(u32, f32)>)];

/// A passage from any list, with its place in each.
#[derive(Debug, Clone, Default)]
pub struct Cand {
    /// The RRF score with the entity boost, as `fused` computed it before learned fusion.
    pub rrf: f32,
    /// Rank in the keyword list of all packs, rank in its own pack's list, BM25 score.
    pub s: Option<(u32, u32, f32)>,
    /// The same for the semantic lists.
    pub d: Option<(u32, u32, f32)>,
    /// Rank of the linked entity whose lead passage this is.
    pub e: Option<u32>,
}

/// Every passage of the lists, with RRF scores and ranks.
pub fn candidates(sparse: &Lists, dense: &Lists, entities: &[Entity], rrf_k: f32, w_sparse: f32, w_dense: f32, w_entity: f32) -> HashMap<(u8, u32), Cand> {
    let mut out: HashMap<(u8, u32), Cand> = HashMap::new();
    for (is_dense, w, lists) in [(false, w_sparse, sparse), (true, w_dense, dense)] {
        // Dense scores compare across packs (one embedder); BM25 scores only roughly, because each index has
        // its own term statistics.
        let mut merged: Vec<(u8, u32, u32, f32)> =
            lists.iter().flat_map(|(p, l)| l.iter().enumerate().map(move |(r, &(id, s))| (*p, id, r as u32, s))).collect();
        merged.sort_unstable_by(|a, b| b.3.total_cmp(&a.3));
        for (rank, &(pack, pid, prank, score)) in merged.iter().enumerate() {
            let c = out.entry((pack, pid)).or_default();
            c.rrf += w / (rrf_k + rank as f32 + 1.0);
            let place = Some((rank as u32, prank, score));
            if is_dense { c.d = place } else { c.s = place }
        }
    }
    for (rank, e) in entities.iter().enumerate() {
        let c = out.entry((e.pack, e.article.first_passage)).or_default();
        c.rrf += w_entity / (rrf_k + rank as f32 + 1.0);
        c.e = Some(rank as u32);
    }
    out
}

/// Per-query facts that features compare a candidate against.
pub struct Ctx {
    s_top: f32,
    s_pack: HashMap<u8, PackStats>,
    d_top: f32,
    d_pack: HashMap<u8, PackStats>,
    q_len: f32,
    /// Share of the best 50 keyword (semantic) hits that each pack holds: a pack that floods a list.
    s_share: HashMap<u8, f32>,
    d_share: HashMap<u8, f32>,
    pack_size: Vec<f32>,
    keyword_only: Vec<bool>,
}

const SHARE_TOP: usize = 50;

/// A pack's own list: best score, mean and spread.
#[derive(Default, Clone, Copy)]
struct PackStats {
    top: f32,
    mean: f32,
    sd: f32,
}

impl Ctx {
    pub fn new(lib: &Library, query: &str, sparse: &Lists, dense: &Lists) -> Self {
        let top = |lists: &Lists| -> (f32, HashMap<u8, PackStats>, HashMap<u8, f32>) {
            let mut all: Vec<(u8, f32)> = lists.iter().flat_map(|(p, l)| l.iter().map(move |x| (*p, x.1))).collect();
            all.sort_unstable_by(|a, b| b.1.total_cmp(&a.1));
            let mut share: HashMap<u8, f32> = HashMap::new();
            let n = all.len().min(SHARE_TOP).max(1) as f32;
            for (p, _) in all.iter().take(SHARE_TOP) {
                *share.entry(*p).or_default() += 1.0 / n;
            }
            let pack_top = lists
                .iter()
                .filter_map(|(p, l)| {
                    let n = l.len() as f32;
                    let mean = l.iter().map(|x| x.1).sum::<f32>() / n;
                    let sd = (l.iter().map(|x| (x.1 - mean).powi(2)).sum::<f32>() / n).sqrt();
                    l.first().map(|x| (*p, PackStats { top: x.1, mean, sd }))
                })
                .collect();
            (all.first().map_or(0.0, |x| x.1), pack_top, share)
        };
        let (s_top, s_pack, s_share) = top(sparse);
        let (d_top, d_pack, d_share) = top(dense);
        let norm = (1.0f32 + 1e7).ln();
        Self {
            s_top,
            s_pack,
            d_top,
            d_pack,
            q_len: (1.0 + query.split_whitespace().count() as f32).ln(),
            s_share,
            d_share,
            pack_size: lib.packs.iter().map(|p| (1.0 + p.manifest.counts.as_ref().map_or(0, |c| c.passages) as f32).ln() / norm).collect(),
            keyword_only: lib.packs.iter().map(|p| p.dense.is_none()).collect(),
        }
    }
}

/// Features known before the passages load. The order matches `CHEAP`.
pub const CHEAP: &[&str] = &[
    "rrf", "s_in", "s_rr60", "s_rr5", "s_lr", "s_prr5", "s_plr", "s_pnorm", "s_gnorm", "d_in", "d_rr60", "d_rr5", "d_lr", "d_prr5",
    "d_plr", "d_norm", "both", "e_in", "e_rr", "kw_only", "s_share", "d_share", "pack_size", "s_rr60_share", "d_pnorm", "s_pz",
    "d_pz", "s_ptop", "d_ptop", "q_len", "s_top", "d_top", "s_rr60_qlen", "d_rr60_qlen",
];

pub fn cheap(pack: u8, c: &Cand, x: &Ctx) -> Vec<f32> {
    let rr = |p: Option<(u32, u32, f32)>, k: f32, pack_rank: bool| p.map_or(0.0, |(r, pr, _)| 1.0 / (k + 1.0 + if pack_rank { pr } else { r } as f32));
    // A missing rank counts as rank 1000.
    let lr = |p: Option<(u32, u32, f32)>, pack_rank: bool| (1.0 + p.map_or(1000, |(r, pr, _)| if pack_rank { pr } else { r }) as f32).ln();
    let ratio = |a: f32, b: f32| if b > 0.0 { a / b } else { 0.0 };
    let s_share = x.s_share.get(&pack).copied().unwrap_or(0.0);
    let (sp, dp) = (x.s_pack.get(&pack).copied().unwrap_or_default(), x.d_pack.get(&pack).copied().unwrap_or_default());
    let z = |v: Option<(u32, u32, f32)>, st: PackStats| v.map_or(-3.0, |v| if st.sd > 0.0 { ((v.2 - st.mean) / st.sd).clamp(-3.0, 5.0) } else { 0.0 });
    vec![
        c.rrf * 100.0,
        c.s.is_some() as u8 as f32,
        rr(c.s, 60.0, false),
        rr(c.s, 5.0, false),
        lr(c.s, false),
        rr(c.s, 5.0, true),
        lr(c.s, true),
        c.s.map_or(0.0, |s| ratio(s.2, sp.top)),
        c.s.map_or(0.0, |s| ratio(s.2, x.s_top)),
        c.d.is_some() as u8 as f32,
        rr(c.d, 60.0, false),
        rr(c.d, 5.0, false),
        lr(c.d, false),
        rr(c.d, 5.0, true),
        lr(c.d, true),
        c.d.map_or(0.0, |d| ratio(d.2, x.d_top)),
        (c.s.is_some() && c.d.is_some()) as u8 as f32,
        c.e.is_some() as u8 as f32,
        c.e.map_or(0.0, |r| 1.0 / (1.0 + r as f32)),
        x.keyword_only.get(pack as usize).copied().unwrap_or(false) as u8 as f32,
        s_share,
        x.d_share.get(&pack).copied().unwrap_or(0.0),
        x.pack_size.get(pack as usize).copied().unwrap_or(0.0),
        rr(c.s, 60.0, false) * s_share,
        c.d.map_or(0.0, |d| ratio(d.2, dp.top)),
        z(c.s, sp),
        z(c.d, dp),
        ratio(sp.top, x.s_top),
        ratio(dp.top, x.d_top),
        x.q_len,
        (1.0 + x.s_top.max(0.0)).ln(),
        x.d_top,
        rr(c.s, 60.0, false) * x.q_len,
        rr(c.d, 60.0, false) * x.q_len,
    ]
}

/// Features of the loaded passage. The order matches `LOADED`.
pub const LOADED: &[&str] = &["pop", "lead", "ord_log", "len_log", "has_section"];

pub fn loaded(h: &Hit) -> Vec<f32> {
    let norm = (1.0f32 + 1e7).ln();
    vec![
        ((1.0 + h.article.popularity as f32).ln() / norm).min(1.0),
        (h.passage.ordinal == 0) as u8 as f32,
        (1.0 + h.passage.ordinal as f32).ln(),
        (1.0 + h.passage.text.chars().count() as f32).ln(),
        (!h.passage.section_path.is_empty()) as u8 as f32,
    ]
}

/// A linear score over every feature, plus a tanh layer over the features known before the load. The file
/// names each weight's feature; a feature without a weight gets 0.
#[derive(serde::Deserialize)]
struct ModelFile {
    w: HashMap<String, f32>,
    mlp: Option<MlpFile>,
}

#[derive(serde::Deserialize)]
struct MlpFile {
    inputs: Vec<String>,
    w1: Vec<Vec<f32>>,
    b1: Vec<f32>,
    w2: Vec<f32>,
}

pub struct Model {
    cheap: Vec<f32>,
    loaded: Vec<f32>,
    /// Hidden units: weights in `CHEAP` order, bias, output weight.
    hidden: Vec<(Vec<f32>, f32, f32)>,
}

static MODEL: std::sync::LazyLock<Model> = std::sync::LazyLock::new(|| {
    let f: ModelFile = serde_json::from_str(include_str!("fusion_model.json")).expect("fusion_model.json");
    for k in f.w.keys() {
        assert!(CHEAP.contains(&k.as_str()) || LOADED.contains(&k.as_str()), "fusion_model.json: unknown feature {k}");
    }
    let pick = |names: &[&str]| names.iter().map(|n| f.w.get(*n).copied().unwrap_or(0.0)).collect();
    let hidden = f.mlp.map_or(vec![], |m| {
        let col: Vec<usize> = m.inputs.iter().map(|n| CHEAP.iter().position(|c| c == n).unwrap_or_else(|| panic!("fusion_model.json: {n} is not a cheap feature"))).collect();
        m.w1.iter()
            .zip(&m.b1)
            .zip(&m.w2)
            .map(|((row, b), w2)| {
                let mut w = vec![0.0; CHEAP.len()];
                for (c, v) in col.iter().zip(row) {
                    w[*c] = *v;
                }
                (w, *b, *w2)
            })
            .collect()
    });
    Model { cheap: pick(CHEAP), loaded: pick(LOADED), hidden }
});

fn dot(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| x * y).sum()
}

impl Model {
    pub fn get() -> &'static Model {
        &MODEL
    }

    pub fn cheap_score(&self, f: &[f32]) -> f32 {
        dot(&self.cheap, f) + self.hidden.iter().map(|(w, b, w2)| w2 * (dot(w, f) + b).tanh()).sum::<f32>()
    }

    pub fn loaded_score(&self, f: &[f32]) -> f32 {
        dot(&self.loaded, f)
    }
}
