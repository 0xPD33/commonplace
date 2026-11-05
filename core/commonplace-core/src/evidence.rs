//! Stage 3: evidence assembly within a token budget. Fact cards first, else a sentence window.

use crate::library::Library;
use crate::pack::Fact;
use crate::retrieval::Hit;
use crate::text::{approx_tokens, numbers, sentences, terms};
use crate::tools::wikidata::WdFact;
use std::collections::HashSet;

#[derive(Debug, Clone)]
pub struct Source {
    pub n: u32,
    pub hit: Hit,
    /// The text the model saw for this source.
    pub shown: String,
    pub from_cards: bool,
}

/// One entity's Wikidata facts: a numbered source after the passages.
#[derive(Debug, Clone)]
pub struct WdSource {
    pub n: u32,
    pub facts: Vec<WdFact>,
    /// The fact lines the model saw.
    pub shown: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct Evidence {
    pub sources: Vec<Source>,
    pub wikidata: Vec<WdSource>,
    pub computed: Vec<String>,
    /// Normalized numbers present anywhere in the evidence.
    pub numbers: HashSet<String>,
}

impl Evidence {
    pub fn render(&self, lib: &Library) -> String {
        let mut s = String::new();
        for src in &self.sources {
            let pack = &lib.packs[src.hit.pack as usize].manifest.pack_id;
            let h = &src.hit;
            if h.passage.section_path.is_empty() {
                s.push_str(&format!("[{}] {} ({}):\n{}\n\n", src.n, h.article.title, pack, src.shown));
            } else {
                s.push_str(&format!("[{}] {} — {} ({}):\n{}\n\n", src.n, h.article.title, h.passage.section_path, pack, src.shown));
            }
        }
        // A fact without a date is the value current at the snapshot, not in a year the question names.
        let (pack, snapshot) = lib.wikidata.as_ref().map_or(("wikidata", ""), |(m, _)| (m.pack_id.as_str(), m.snapshot_date.as_str()));
        for w in &self.wikidata {
            s.push_str(&format!("[{}] {} ({pack}, as of {snapshot}):\n", w.n, w.facts[0].entity));
            for line in &w.shown {
                s.push_str(&format!("- {line}\n"));
            }
            s.push('\n');
        }
        for c in &self.computed {
            s.push_str(&format!("COMPUTED: {c}\n"));
        }
        s.trim_end().to_string()
    }

    /// Passages and Wikidata entities: the valid citation numbers are 1 to this.
    pub fn count(&self) -> u32 {
        (self.sources.len() + self.wikidata.len()) as u32
    }

    pub fn add_computed(&mut self, line: String) {
        self.numbers.extend(numbers(&line));
        self.computed.push(line);
    }
}

/// Choose the best 2-4 sentences by query-term overlap plus a small lead bonus, kept in text order.
pub fn sentence_window(text: &str, qterms: &HashSet<String>, max_tokens: usize) -> String {
    if approx_tokens(text) <= max_tokens {
        return text.to_string();
    }
    let sents: Vec<&str> = sentences(text).into_iter().flat_map(|s| split_long(s, max_tokens / 2)).collect();
    let scored: Vec<(usize, f32)> = sents
        .iter()
        .enumerate()
        .map(|(i, s)| {
            let t = terms(s);
            let overlap = t.iter().filter(|w| qterms.contains(*w)).count() as f32;
            (i, overlap + if i == 0 { 0.5 } else { 0.0 })
        })
        .collect();
    let mut order = scored.clone();
    order.sort_by(|a, b| b.1.total_cmp(&a.1));
    let mut pick: Vec<usize> = Vec::new();
    let mut used = 0;
    for (i, _) in order {
        let n = approx_tokens(sents[i]);
        if pick.len() >= 2 && used + n > max_tokens {
            continue;
        }
        pick.push(i);
        used += n;
        if pick.len() >= 4 || used >= max_tokens {
            break;
        }
    }
    pick.sort_unstable();
    let mut out = String::new();
    let mut last: Option<usize> = None;
    for i in pick {
        if let Some(l) = last
            && i > l + 1
        {
            out.push_str(" … ");
        } else if last.is_some() {
            out.push(' ');
        }
        out.push_str(sents[i]);
        last = Some(i);
    }
    out
}

/// The sentence of `text` that holds the best-scored piece: the card highlight when the sentence head ran.
pub fn best_scored_sentence(text: &str, scores: &[Option<f32>]) -> Option<String> {
    let parts = pieces(text);
    if parts.len() != scores.len() {
        return None;
    }
    let best = parts.iter().zip(scores).filter_map(|(p, s)| s.map(|s| (p, s))).max_by(|a, b| a.1.total_cmp(&b.1))?.0;
    let at = best.as_ptr() as usize - text.as_ptr() as usize;
    sentences(text).into_iter().find(|s| {
        let a = s.as_ptr() as usize - text.as_ptr() as usize;
        a <= at && at < a + s.len()
    }).map(str::to_string)
}

/// The longest piece of a sentence: half the 120-token window of `sentence_window`.
const PIECE_TOKENS: usize = 60;

/// The units that sentence selection scores and picks: sentences, with long ones cut into pieces.
pub fn pieces(text: &str) -> Vec<&str> {
    sentences(text).into_iter().flat_map(|s| split_long(s, PIECE_TOKENS)).collect()
}

/// Byte ranges of the passage's `pieces` in `Hit::rerank_text`, which ends with the passage text.
pub fn piece_spans(h: &Hit) -> Vec<(usize, usize)> {
    let text = &h.passage.text;
    let prefix = h.rerank_text().len() - text.len();
    pieces(text)
        .into_iter()
        .map(|p| {
            let a = p.as_ptr() as usize - text.as_ptr() as usize + prefix;
            (a, a + p.len())
        })
        .collect()
}

/// The contiguous stretch of `text` around its best-scored piece, grown toward the better-scored neighbor up to
/// `max_tokens`: one coherent stretch per passage, with its line breaks. `None` when the scores do not fit the pieces.
fn head_window(text: &str, scores: &[Option<f32>], max_tokens: usize) -> Option<String> {
    let parts = pieces(text);
    if parts.len() != scores.len() {
        return None;
    }
    if approx_tokens(text) <= max_tokens {
        return Some(text.to_string());
    }
    let sc = |i: usize| scores[i].unwrap_or(f32::MIN);
    let best = (0..parts.len()).max_by(|&x, &y| sc(x).total_cmp(&sc(y)))?;
    let (mut a, mut b, mut used) = (best, best, approx_tokens(parts[best]));
    loop {
        let fits = |i: usize| used + approx_tokens(parts[i]) <= max_tokens;
        let left = (a > 0 && fits(a - 1)).then(|| a - 1);
        let right = (b + 1 < parts.len() && fits(b + 1)).then_some(b + 1);
        let next = match (left, right) {
            (Some(l), Some(r)) => if sc(l) >= sc(r) { l } else { r },
            (Some(i), None) | (None, Some(i)) => i,
            (None, None) => break,
        };
        used += approx_tokens(parts[next]);
        if next < a { a = next } else { b = next }
    }
    let at = |p: &str| p.as_ptr() as usize - text.as_ptr() as usize;
    Some(text[at(parts[a])..at(parts[b]) + parts[b].len()].to_string())
}

/// A "sentence" longer than `limit` tokens, such as a table flattened to text, cut at spaces into pieces that fit.
fn split_long(s: &str, limit: usize) -> Vec<&str> {
    let mut out = Vec::new();
    let mut rest = s;
    while approx_tokens(rest) > limit {
        let cut = rest.match_indices(' ').map(|(i, _)| i).take_while(|&i| approx_tokens(&rest[..i]) <= limit).last().unwrap_or(0);
        if cut == 0 {
            break;
        }
        out.push(rest[..cut].trim());
        rest = rest[cut..].trim_start();
    }
    out.push(rest);
    out
}

/// The sentence of `text` that best matches the query, for the card highlight. The first of equal matches wins.
pub fn best_sentence(text: &str, query: &str) -> String {
    let q: HashSet<String> = terms(query).into_iter().collect();
    sentences(text)
        .into_iter()
        .rev()
        .map(|s| (s, terms(s).iter().filter(|w| q.contains(*w)).count()))
        .max_by_key(|(_, c)| *c)
        .map(|(s, _)| s.to_string())
        .unwrap_or_default()
}

/// `wikidata`: facts grouped by entity; each entity becomes one numbered source after the passages.
pub fn assemble(lib: &Library, hits: &[Hit], query: &str, budget_tokens: usize, wikidata: Vec<WdFact>) -> Evidence {
    let qterms: HashSet<String> = terms(query).into_iter().collect();
    let mut ev = Evidence::default();
    let mut groups: Vec<(Vec<WdFact>, Vec<String>)> = Vec::new();
    for f in wikidata {
        let line = f.short_line();
        match groups.last_mut().filter(|(g, _)| g[0].qid == f.qid) {
            Some((g, shown)) => {
                g.push(f);
                shown.push(line);
            }
            None => groups.push((vec![f], vec![line])),
        }
    }
    let mut used = groups.iter().map(|(g, shown)| approx_tokens(&g[0].entity) + 14 + shown.iter().map(|l| approx_tokens(l) + 2).sum::<usize>()).sum::<usize>();
    for h in hits {
        let facts: Vec<Fact> = lib.packs[h.pack as usize].facts(h.pid).unwrap_or_default();
        let (shown, from_cards) = if facts.is_empty() {
            let head = h.sentence_scores.as_deref().and_then(|sc| head_window(&h.passage.text, sc, 120));
            (head.unwrap_or_else(|| sentence_window(&h.passage.text, &qterms, 120)), false)
        } else {
            (facts.iter().map(|f| format!("- {}", f.text)).collect::<Vec<_>>().join("\n"), true)
        };
        let cost = approx_tokens(&shown) + 12;
        if used + cost > budget_tokens && !ev.sources.is_empty() {
            continue;
        }
        used += cost;
        // The whole passage counts: the windows shrink with the budget, but a number in the passage is still from the source.
        ev.numbers.extend(numbers(&h.passage.text));
        ev.numbers.extend(numbers(&h.article.title));
        ev.sources.push(Source { n: ev.sources.len() as u32 + 1, hit: h.clone(), shown, from_cards });
    }
    for (facts, shown) in groups {
        for line in &shown {
            ev.numbers.extend(numbers(line));
        }
        ev.wikidata.push(WdSource { n: ev.count() + 1, facts, shown });
    }
    if let Some((m, _)) = lib.wikidata.as_ref().filter(|_| !ev.wikidata.is_empty()) {
        ev.numbers.extend(numbers(&m.snapshot_date));
    }
    ev
}
