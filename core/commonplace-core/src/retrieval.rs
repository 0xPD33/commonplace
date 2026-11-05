//! Stage 1: hybrid retrieval (BM25 + binary IVF), fusion (RRF with a popularity prior, or learned), rerank.

use crate::encoders::{QueryEncoder, Reranker};
use crate::fusion;
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
    /// Lists a dense index probes at most.
    pub nprobe: usize,
    /// Codes a dense index scans: each pack probes as many lists as hold about this many codes (at most
    /// `nprobe`). Packs with long lists (Stack Exchange) would otherwise scan five times Wikipedia's codes.
    pub probe_codes: usize,
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
    /// Score each sentence piece with the sentence head during the rerank; the evidence then keeps the best
    /// pieces of all passages instead of a word-overlap window per passage.
    pub sentence_head: bool,
    pub fusion: Fusion,
}

/// How `fused` orders candidates before the cut to the rerank pool.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Fusion {
    Rrf,
    Learned,
}

impl Default for RetrievalSettings {
    fn default() -> Self {
        Self {
            sparse_k: 200,
            dense_k: 200,
            hamming_k: 1000,
            nprobe: 48,
            probe_codes: 48_000,
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
            sentence_head: true,
            fusion: Fusion::Learned,
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
    /// Sentence-head logits of `evidence::pieces(&passage.text)` for the rerank query; `None` for a piece past the
    /// reranker's 256-token cut.
    pub sentence_scores: Option<Vec<Option<f32>>>,
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
    /// Step 1: sparse and dense search in parallel. One list per pack, best first.
    fn search(&self, query: &str, st: &mut RetrievalStats) -> Result<(Vec<(u8, Vec<(u32, f32)>)>, Vec<(u8, Vec<(u32, f32)>)>)> {
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
                use rayon::prelude::*;
                let packs: Vec<(u8, &crate::pack::dense::DenseIndex)> = self.lib.scoped(self.scope).filter_map(|(i, p)| p.dense.as_ref().map(|d| (i, d))).collect();
                let r = packs.par_iter().map(|(i, d)| (*i, d.search(&q, d.lists_for(self.s.probe_codes).min(self.s.nprobe), self.s.hamming_k, self.s.dense_k))).collect();
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
        Ok((sparse, dense))
    }

    fn candidates(&self, sparse: &fusion::Lists, dense: &fusion::Lists, entities: &[Entity]) -> HashMap<(u8, u32), fusion::Cand> {
        fusion::candidates(sparse, dense, entities, self.s.rrf_k, self.s.w_sparse, self.s.w_dense, self.s.w_entity)
    }

    /// Steps 1-4: search, entity boost, fusion (RRF and a popularity prior, or the learned scorer). Returns
    /// fused hits.
    pub fn fused(&self, query: &str, entities: &[Entity], st: &mut RetrievalStats) -> Result<Vec<Hit>> {
        let (sparse, dense) = self.search(query, st)?;
        let t = Instant::now();
        let cands = self.candidates(&sparse, &dense, entities);
        let learned = self.s.fusion == Fusion::Learned;
        let fused: HashMap<(u8, u32), f32> = if learned {
            // The scorer saw only the candidates near a list's top in training (`candidate_dump`).
            let (ctx, m) = (fusion::Ctx::new(self.lib, query, &sparse, &dense), fusion::Model::get());
            Self::near_top(&cands, fusion::DEPTH).into_iter().map(|(k, c)| (*k, m.cheap_score(&fusion::cheap(k.0, c, &ctx)))).collect()
        } else {
            cands.iter().map(|(k, c)| (*k, c.rrf)).collect()
        };
        let mut top: Vec<((u8, u32), f32)> = fused.iter().map(|(k, v)| (*k, *v)).collect();
        top.sort_unstable_by(|a, b| b.1.total_cmp(&a.1));
        top.truncate(self.s.fuse_keep * 3);
        // Packs without dense codes appear in one list only; keep each pack's best keyword hits in the
        // pool so the reranker sees them. The learned scorer has no entity boost: keep linked entities' leads.
        let keyword_only = |p: u8| self.lib.packs[p as usize].dense.is_none();
        let mut pinned: Vec<(u8, u32)> =
            sparse.iter().filter(|(p, _)| keyword_only(*p)).flat_map(|(p, l)| l.iter().take(self.s.keyword_only_keep).map(move |&(id, _)| (*p, id))).collect();
        if learned {
            pinned.extend(entities.iter().map(|e| (e.pack, e.article.first_passage)));
        }
        for k in &pinned {
            if !top.iter().any(|(t, _)| t == k) {
                top.push((*k, fused.get(k).copied().unwrap_or(0.0)));
            }
        }
        let mut hits = self.load(&top)?;
        let norm = (1.0f32 + 1e7).ln();
        for h in &mut hits {
            h.fused += if learned {
                fusion::Model::get().loaded_score(&fusion::loaded(h))
            } else {
                self.s.prior_weight * ((1.0 + h.article.popularity as f32).ln() / norm).min(1.0)
            };
        }
        hits.sort_unstable_by(|a, b| b.fused.total_cmp(&a.fused));
        // Leave room in the rerank pool for other articles: at most four passages per article.
        let mut per_article: HashMap<(u8, u32), usize> = HashMap::new();
        hits.retain(|h| {
            let c = per_article.entry((h.pack, h.article.id)).or_default();
            *c += 1;
            *c <= 4
        });
        let mut rank = 0;
        hits.retain(|h| {
            rank += 1;
            rank <= self.s.fuse_keep || pinned.contains(&(h.pack, h.pid))
        });
        if learned {
            // Learned scores carry per-query offsets; a comparison merges several queries' pools by `fused`,
            // so give each hit an RRF-like value of its rank instead.
            for (rank, h) in hits.iter_mut().enumerate() {
                h.fused = 1.0 / (self.s.rrf_k + rank as f32 + 1.0);
            }
        }
        st.n_fused = hits.len();
        st.fuse_ms = ms(t);
        Ok(hits)
    }

    /// Candidates within `depth` of the top of a list or of RRF, among their pack's best 10, or linked
    /// entities, by RRF.
    fn near_top(cands: &HashMap<(u8, u32), fusion::Cand>, depth: usize) -> Vec<(&(u8, u32), &fusion::Cand)> {
        let mut by_rrf: Vec<(&(u8, u32), &fusion::Cand)> = cands.iter().collect();
        by_rrf.sort_unstable_by(|a, b| b.1.rrf.total_cmp(&a.1.rrf).then(a.0.cmp(b.0)));
        let near = |p: Option<(u32, u32, f32)>| p.is_some_and(|(r, pr, _)| (r as usize) < depth || pr < 10);
        by_rrf.into_iter().enumerate().filter(|(i, (_, c))| *i < depth || near(c.s) || near(c.d) || c.e.is_some()).map(|(_, x)| x).collect()
    }

    /// Training data for learned fusion: every candidate within `depth` of the top of a list (or of RRF), or
    /// among its pack's best 10, loaded, with its features (`fusion::CHEAP` then `fusion::LOADED`). `fused`
    /// holds the RRF score with the popularity prior.
    pub fn candidate_dump(&self, query: &str, entities: &[Entity], depth: usize) -> Result<Vec<(Hit, Vec<f32>)>> {
        let mut st = RetrievalStats::default();
        let (sparse, dense) = self.search(query, &mut st)?;
        let cands = self.candidates(&sparse, &dense, entities);
        let ctx = fusion::Ctx::new(self.lib, query, &sparse, &dense);
        let keep: Vec<((u8, u32), f32)> = Self::near_top(&cands, depth).into_iter().map(|(k, c)| (*k, c.rrf)).collect();
        let mut hits = self.load(&keep)?;
        let norm = (1.0f32 + 1e7).ln();
        Ok(hits
            .drain(..)
            .map(|mut h| {
                h.fused += self.s.prior_weight * ((1.0 + h.article.popularity as f32).ln() / norm).min(1.0);
                let mut f = fusion::cheap(h.pack, &cands[&(h.pack, h.pid)], &ctx);
                f.extend(fusion::loaded(&h));
                (h, f)
            })
            .collect())
    }

    fn pack_penalty(&self, pack: u8) -> f32 {
        if self.lib.packs[pack as usize].manifest.pack_id.starts_with("wikiquote") { QUOTE_PENALTY } else { 0.0 }
    }

    /// Load passages and article metadata for (pack, pid) pairs, preserving order.
    pub fn load(&self, ids: &[((u8, u32), f32)]) -> Result<Vec<Hit>> {
        let mut by_pack: HashMap<u8, Vec<(usize, u32)>> = HashMap::new();
        for (i, ((pack, pid), _)) in ids.iter().enumerate() {
            by_pack.entry(*pack).or_default().push((i, *pid));
        }
        use rayon::prelude::*;
        let per_pack: Vec<Vec<(usize, Hit)>> = by_pack
            .into_par_iter()
            .map(|(pack, items)| -> Result<Vec<(usize, Hit)>> {
                let p = &self.lib.packs[pack as usize];
                let pids: Vec<u32> = items.iter().map(|x| x.1).collect();
                let recs = p.passages(&pids)?;
                let mut aids: Vec<u32> = recs.iter().map(|r| r.article_id).collect();
                aids.sort_unstable();
                aids.dedup();
                let arts: HashMap<u32, Article> = p.meta.articles(&aids)?.into_iter().map(|a| (a.id, a)).collect();
                Ok(items
                    .into_iter()
                    .zip(recs)
                    .filter_map(|((i, pid), rec)| {
                        let a = arts.get(&rec.article_id)?.clone();
                        Some((i, Hit { pack, pid, article: a, passage: rec, fused: ids[i].1, rerank: None, sentence_scores: None }))
                    })
                    .collect())
            })
            .collect::<Result<_>>()?;
        let mut out: Vec<Option<Hit>> = vec![None; ids.len()];
        for (i, h) in per_pack.into_iter().flatten() {
            out[i] = Some(h);
        }
        Ok(out.into_iter().flatten().collect())
    }

    /// Step 5: cross-encoder rerank; keeps the best `keep`. A hit that already has a rerank score keeps it, so
    /// the caller must clear scores that belong to another query.
    pub fn rerank(&self, query: &str, mut hits: Vec<Hit>, keep: usize, st: &mut RetrievalStats) -> Result<Vec<Hit>> {
        let t = Instant::now();
        if let Some(rr) = self.rr.filter(|_| self.s.use_rerank)
            && !hits.is_empty()
        {
            let todo: Vec<usize> = (0..hits.len()).filter(|&i| hits[i].rerank.is_none()).collect();
            let docs: Vec<String> = todo.iter().map(|&i| hits[i].rerank_text()).collect();
            let spans: Vec<Vec<(usize, usize)>> = if self.s.sentence_head { todo.iter().map(|&i| crate::evidence::piece_spans(&hits[i])).collect() } else { vec![] };
            let (scores, sents) = if docs.is_empty() { (vec![], vec![]) } else { rr.score_spans(query, &docs, &spans)? };
            // `hits` arrive in fusion order, so the index is the retrieval rank.
            let n = hits.len() as f32;
            for ((&i, s), sent) in todo.iter().zip(scores).zip(sents) {
                let blend = self.s.rerank_blend * (1.0 - i as f32 / n);
                hits[i].rerank = Some(s - list_penalty(&hits[i].article.title) - self.pack_penalty(hits[i].pack) + blend);
                hits[i].sentence_scores = self.s.sentence_head.then_some(sent);
            }
            hits.sort_unstable_by(|a, b| b.score().total_cmp(&a.score()));
        }
        st.n_reranked = hits.len();
        hits.truncate(keep);
        st.rerank_top = hits.iter().take(5).map(Hit::score).collect();
        st.rerank_ms = ms(t);
        Ok(hits)
    }

    /// The fused pool, reranked: every scored hit, best first.
    pub fn ranked(&self, query: &str, entities: &[Entity], st: &mut RetrievalStats) -> Result<Vec<Hit>> {
        let hits = self.fused(query, entities, st)?;
        self.rerank(query, hits, usize::MAX, st)
    }
}

/// Pick up to `n` hits, at most `per_article` per article, making sure each `must` article appears once.
/// The `must` hits come first, so an evidence budget cannot cut them; the rest follow by score.
pub fn select_diverse(hits: &[Hit], n: usize, per_article: usize, must: &[(u8, u32)]) -> Vec<Hit> {
    let mut out: Vec<Hit> = Vec::new();
    let mut count: HashMap<(u8, u32), usize> = HashMap::new();
    for m in must {
        if let Some(h) = hits.iter().find(|h| (h.pack, h.article.id) == *m) {
            out.push(h.clone());
            *count.entry(*m).or_default() += 1;
        }
    }
    let pinned = out.len();
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
    out[pinned..].sort_by(|a, b| b.score().total_cmp(&a.score()));
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

/// Rerank penalty for quotation pages (Wikiquote): they repeat the topic's name in every line, so the reranker
/// scores them high, but they seldom state the fact a question asks for.
const QUOTE_PENALTY: f32 = 0.5;
