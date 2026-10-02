//! Stage 1: hybrid retrieval (BM25 + binary IVF), RRF fusion with a popularity prior, rerank.

use crate::encoders::{QueryEncoder, Reranker};
use crate::library::Library;
use crate::pack::{PassageRecord, meta::Article};
use crate::route::Entity;
use anyhow::Result;
use serde::Serialize;
use std::collections::HashMap;
use std::time::Instant;

#[derive(Debug, Clone, Serialize)]
pub struct RetrievalSettings {
    pub sparse_k: usize,
    pub dense_k: usize,
    pub hamming_k: usize,
    pub nprobe: usize,
    pub rrf_k: f32,
    pub w_sparse: f32,
    pub w_dense: f32,
    pub w_entity: f32,
    pub prior_weight: f32,
    pub fuse_keep: usize,
    pub rerank_keep: usize,
    /// Best keyword hits of each keyword-only pack that always enter the rerank pool.
    pub keyword_only_keep: usize,
    /// Weight of the retrieval (fusion) rank in the final score: the first fused hit gets `rerank_blend`,
    /// the last gets 0. 0 lets the reranker alone decide.
    pub rerank_blend: f32,
    pub use_sparse: bool,
    pub use_dense: bool,
    pub use_rerank: bool,
}

impl Default for RetrievalSettings {
    fn default() -> Self {
        Self {
            sparse_k: 200,
            dense_k: 200,
            hamming_k: 1000,
            nprobe: 48,
            rrf_k: 60.0,
            w_sparse: 1.0,
            w_dense: 1.0,
            w_entity: 2.0,
            prior_weight: 0.005,
            fuse_keep: 40,
            rerank_keep: 8,
            keyword_only_keep: 5,
            rerank_blend: 0.0,
            use_sparse: true,
            use_dense: true,
            use_rerank: true,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Hit {
    pub pack: u8,
    pub pid: u32,
    pub passage: PassageRecord,
    pub article: Article,
    pub fused: f32,
    pub rerank: Option<f32>,
}

impl Hit {
    pub fn score(&self) -> f32 {
        self.rerank.unwrap_or(self.fused)
    }
    pub fn rerank_text(&self) -> String {
        if self.passage.section_path.is_empty() {
            format!("{}: {}", self.article.title, self.passage.text)
        } else {
            format!("{} — {}: {}", self.article.title, self.passage.section_path, self.passage.text)
        }
    }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct RetrievalStats {
    pub encode_ms: f64,
    pub sparse_ms: f64,
    pub dense_ms: f64,
    pub fuse_ms: f64,
    pub rerank_ms: f64,
    pub n_sparse: usize,
    pub n_dense: usize,
    pub n_fused: usize,
    pub n_reranked: usize,
    pub rerank_top: Vec<f32>,
}

fn ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

pub struct Retriever<'a> {
    pub lib: &'a Library,
    pub enc: Option<&'a QueryEncoder>,
    pub rr: Option<&'a Reranker>,
    pub s: &'a RetrievalSettings,
    /// Pack ids to search; empty means every pack.
    pub scope: &'a [String],
}

impl Retriever<'_> {
    /// Steps 1-4: sparse and dense search in parallel, entity boost, RRF, prior. Returns fused hits.
    pub fn fused(&self, query: &str, entities: &[Entity], st: &mut RetrievalStats) -> Result<Vec<Hit>> {
        let (sparse, dense) = std::thread::scope(|sc| {
            let sparse = sc.spawn(|| {
                let t = Instant::now();
                use rayon::prelude::*;
                let packs: Vec<(u8, &crate::pack::Pack)> = self.lib.scoped(self.scope).collect();
                let r: Vec<(u8, Vec<(u32, f32)>)> = packs
                    .par_iter()
                    .filter(|_| self.s.use_sparse)
                    .filter_map(|(i, p)| p.sparse.as_ref().map(|s| (*i, s.search(query, self.s.sparse_k).unwrap_or_default())))
                    .collect();
                (r, ms(t))
            });
            let dense = sc.spawn(|| -> Result<(Vec<(u8, Vec<(u32, f32)>)>, f64, f64)> {
                let Some(enc) = self.enc.filter(|_| self.s.use_dense) else { return Ok((vec![], 0.0, 0.0)) };
                if !self.lib.scoped(self.scope).any(|(_, p)| p.dense.is_some()) {
                    return Ok((vec![], 0.0, 0.0));
                }
                let t = Instant::now();
                let q = enc.encode(query)?;
                let enc_ms = ms(t);
                let t = Instant::now();
                let r = self
                    .lib
                    .scoped(self.scope)
                    .filter_map(|(i, p)| p.dense.as_ref().map(|d| (i, d.search(&q, self.s.nprobe, self.s.hamming_k, self.s.dense_k))))
                    .collect();
                Ok((r, enc_ms, ms(t)))
            });
            (sparse.join().unwrap(), dense.join().unwrap())
        });
        let (sparse, sparse_ms) = sparse;
        let (dense, encode_ms, dense_ms) = dense?;
        st.sparse_ms = sparse_ms;
        st.dense_ms = dense_ms;
        st.encode_ms = encode_ms;
        st.n_sparse = sparse.iter().map(|x| x.1.len()).sum();
        st.n_dense = dense.iter().map(|x| x.1.len()).sum();

        let t = Instant::now();
        let mut fused: HashMap<(u8, u32), f32> = HashMap::new();
        let k = self.s.rrf_k;
        for (w, lists) in [(self.s.w_sparse, &sparse), (self.s.w_dense, &dense)] {
            // One ranked list per source: packs share the scorer, so their scores compare directly.
            let mut merged: Vec<(u8, u32, f32)> = lists.iter().flat_map(|(p, l)| l.iter().map(move |&(id, s)| (*p, id, s))).collect();
            merged.sort_unstable_by(|a, b| b.2.total_cmp(&a.2));
            for (rank, (pack, pid, _)) in merged.iter().enumerate() {
                *fused.entry((*pack, *pid)).or_default() += w / (k + rank as f32 + 1.0);
            }
        }
        for (rank, e) in entities.iter().enumerate() {
            *fused.entry((e.pack, e.article.first_passage)).or_default() += self.s.w_entity / (k + rank as f32 + 1.0);
        }
        let mut top: Vec<((u8, u32), f32)> = fused.iter().map(|(k, v)| (*k, *v)).collect();
        top.sort_unstable_by(|a, b| b.1.total_cmp(&a.1));
        top.truncate(self.s.fuse_keep * 3);
        // Packs without dense codes appear in one list only; keep each pack's best keyword hits in the
        // pool so the reranker sees them.
        let keyword_only = |p: u8| self.lib.packs[p as usize].dense.is_none();
        for (pack, list) in sparse.iter().filter(|(p, _)| keyword_only(*p)) {
            for &(pid, _) in list.iter().take(self.s.keyword_only_keep) {
                if !top.iter().any(|((p, i), _)| *p == *pack && *i == pid) {
                    top.push(((*pack, pid), fused.get(&(*pack, pid)).copied().unwrap_or(0.0)));
                }
            }
        }
        let mut hits = self.load(&top)?;
        let norm = (1.0f32 + 1e7).ln();
        for h in &mut hits {
            h.fused += self.s.prior_weight * ((1.0 + h.article.popularity as f32).ln() / norm).min(1.0);
        }
        hits.sort_unstable_by(|a, b| b.fused.total_cmp(&a.fused));
        // Leave room in the rerank pool for other articles: at most four passages per article.
        let mut per_article: HashMap<(u8, u32), usize> = HashMap::new();
        hits.retain(|h| {
            let c = per_article.entry((h.pack, h.article.id)).or_default();
            *c += 1;
            *c <= 4
        });
        let best_keyword: std::collections::HashSet<(u8, u32)> =
            sparse.iter().filter(|(p, _)| keyword_only(*p)).flat_map(|(p, l)| l.iter().take(self.s.keyword_only_keep).map(move |&(id, _)| (*p, id))).collect();
        let mut rank = 0;
        hits.retain(|h| {
            rank += 1;
            rank <= self.s.fuse_keep || best_keyword.contains(&(h.pack, h.pid))
        });
        st.n_fused = hits.len();
        st.fuse_ms = ms(t);
        Ok(hits)
    }

    /// Load passages and article metadata for (pack, pid) pairs, preserving order.
    pub fn load(&self, ids: &[((u8, u32), f32)]) -> Result<Vec<Hit>> {
        let mut by_pack: HashMap<u8, Vec<(usize, u32)>> = HashMap::new();
        for (i, ((pack, pid), _)) in ids.iter().enumerate() {
            by_pack.entry(*pack).or_default().push((i, *pid));
        }
        let mut out: Vec<Option<Hit>> = vec![None; ids.len()];
        for (pack, items) in by_pack {
            let p = &self.lib.packs[pack as usize];
            let pids: Vec<u32> = items.iter().map(|x| x.1).collect();
            let recs = p.passages(&pids)?;
            let mut aids: Vec<u32> = recs.iter().map(|r| r.article_id).collect();
            aids.sort_unstable();
            aids.dedup();
            let arts: HashMap<u32, Article> = p.meta.articles(&aids)?.into_iter().map(|a| (a.id, a)).collect();
            for ((i, pid), rec) in items.into_iter().zip(recs) {
                if let Some(a) = arts.get(&rec.article_id) {
                    out[i] = Some(Hit { pack, pid, article: a.clone(), passage: rec, fused: ids[i].1, rerank: None });
                }
            }
        }
        Ok(out.into_iter().flatten().collect())
    }

    /// Step 5: cross-encoder rerank; keeps the best `keep`.
    pub fn rerank(&self, query: &str, mut hits: Vec<Hit>, keep: usize, st: &mut RetrievalStats) -> Result<Vec<Hit>> {
        let t = Instant::now();
        if let Some(rr) = self.rr.filter(|_| self.s.use_rerank)
            && !hits.is_empty()
        {
            let docs: Vec<String> = hits.iter().map(Hit::rerank_text).collect();
            let scores = rr.score(query, &docs)?;
            // `hits` arrive in fusion order, so the index is the retrieval rank.
            let n = hits.len() as f32;
            for (i, (h, s)) in hits.iter_mut().zip(scores).enumerate() {
                let blend = self.s.rerank_blend * (1.0 - i as f32 / n);
                h.rerank = Some(s - list_penalty(&h.article.title) + blend);
            }
            hits.sort_unstable_by(|a, b| b.score().total_cmp(&a.score()));
        }
        st.n_reranked = hits.len();
        hits.truncate(keep);
        st.rerank_top = hits.iter().take(5).map(Hit::score).collect();
        st.rerank_ms = ms(t);
        Ok(hits)
    }

    pub fn retrieve(&self, query: &str, entities: &[Entity], keep: usize, st: &mut RetrievalStats) -> Result<Vec<Hit>> {
        let hits = self.fused(query, entities, st)?;
        let ranked = self.rerank(query, hits, usize::MAX, st)?;
        // At most three passages per article, so one long article cannot crowd out the others.
        Ok(select_diverse(&ranked, keep, 3, &[]))
    }
}

/// Pick up to `n` hits, at most `per_article` per article, making sure each `must` article appears once.
pub fn select_diverse(hits: &[Hit], n: usize, per_article: usize, must: &[(u8, u32)]) -> Vec<Hit> {
    let mut out: Vec<Hit> = Vec::new();
    let mut count: HashMap<(u8, u32), usize> = HashMap::new();
    for m in must {
        if let Some(h) = hits.iter().find(|h| (h.pack, h.article.id) == *m) {
            out.push(h.clone());
            *count.entry(*m).or_default() += 1;
        }
    }
    for h in hits {
        if out.len() >= n {
            break;
        }
        if out.iter().any(|o| o.pack == h.pack && o.pid == h.pid) {
            continue;
        }
        let c = count.entry((h.pack, h.article.id)).or_default();
        if *c < per_article {
            *c += 1;
            out.push(h.clone());
        }
    }
    out.sort_by(|a, b| b.score().total_cmp(&a.score()));
    out
}

/// Rerank penalty for list-style pages ("List of inventors", "Timeline of …", "1948 in the United
/// States"): they mention every topic in passing and crowd out the topic's own article on the card.
const LIST_PENALTY: f32 = 0.5;

fn list_penalty(title: &str) -> f32 {
    let t = title.to_lowercase();
    let listy = ["list of ", "lists of ", "timeline of ", "glossary of ", "outline of ", "index of "].iter().any(|p| t.starts_with(p))
        || t.split_once(" in ").is_some_and(|(year, _)| year.len() == 4 && year.bytes().all(|b| b.is_ascii_digit()));
    if listy { LIST_PENALTY } else { 0.0 }
}
