//! Deterministic post-check of the streamed answer: drop citations to missing sources and flag
//! sentences whose numbers are not in the evidence. Replaces a second LLM verification pass.

use crate::text::{numbers, sentences};
use serde::Serialize;
use std::collections::HashSet;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub enum Segment {
    Text(String),
    /// A sentence with at least one number not found in the evidence or COMPUTED lines.
    Unverified(String),
    Cite(u32),
}

/// Split `answer` into segments. Valid citations are 1..=n_sources.
pub fn check(answer: &str, n_sources: u32, evidence_numbers: &HashSet<String>) -> Vec<Segment> {
    let mut out: Vec<Segment> = Vec::new();
    for sent in split_keep_ws(strip_sources_block(answer)) {
        let (plain, cites) = strip_citations(&sent, n_sources);
        let unverified = numbers(&plain).iter().any(|n| !evidence_numbers.contains(n) && !matches_without_trailing_zero(n, evidence_numbers));
        let mut text = String::new();
        for piece in cites {
            match piece {
                Piece::Text(t) => text.push_str(&t),
                Piece::Cite(n) => {
                    flush(&mut out, &mut text, unverified);
                    out.push(Segment::Cite(n));
                }
            }
        }
        flush(&mut out, &mut text, unverified);
    }
    // Citations placed on their own line or after a space attach to the sentence before them.
    for i in 1..out.len() {
        if matches!(out[i], Segment::Cite(_))
            && let Segment::Text(t) | Segment::Unverified(t) = &mut out[i - 1]
        {
            let n = t.trim_end().len();
            t.truncate(n);
        }
    }
    out.retain(|s| !matches!(s, Segment::Text(t) if t.is_empty()));
    out
}

/// The model sometimes ends its answer with a list of sources. The app shows the real ones, so the block
/// goes: the first line that is only a "Sources" or "References" heading (plain, markdown or with a colon)
/// and everything below it. A heading with its entries on the same line ("Sources: [1], [2]") counts only
/// as the last line, so an answer line such as "Sources: wind and solar" stays.
fn strip_sources_block(text: &str) -> &str {
    let mut start = 0;
    for line in text.split_inclusive('\n') {
        let below = &text[start + line.len()..];
        if start > 0 && is_sources_heading(line).is_some_and(|alone| alone || below.trim().is_empty()) {
            let kept = text[..start].trim_end();
            // A rule line ("---") that set the block apart goes with it.
            let last = kept.rsplit('\n').next().unwrap_or("").trim();
            let rule = last.len() >= 3 && last.chars().all(|c| matches!(c, '-' | '_' | '*'));
            let kept = if rule { kept[..kept.len() - last.len()].trim_end() } else { kept };
            return if kept.is_empty() { text } else { kept };
        }
        start += line.len();
    }
    text
}

/// `Some(true)` for a heading alone on its line, `Some(false)` for a heading followed by entries.
fn is_sources_heading(line: &str) -> Option<bool> {
    let decor = |c: char| matches!(c, '#' | '*' | '_' | '>' | '-' | ' ');
    let line = line.trim().trim_matches(decor);
    let (head, rest) = line.split_once(':').unwrap_or((line, ""));
    let head = head.trim_matches(decor).to_lowercase();
    let known = matches!(head.as_str(), "sources" | "source" | "references" | "reference" | "citations" | "bibliography" | "sources cited" | "sources used" | "works cited");
    known.then(|| rest.trim_matches(decor).is_empty())
}

fn flush(out: &mut Vec<Segment>, text: &mut String, unverified: bool) {
    if text.is_empty() {
        return;
    }
    let t = std::mem::take(text);
    let seg = if unverified && t.trim().chars().any(|c| c.is_alphanumeric()) { Segment::Unverified(t) } else { Segment::Text(t) };
    match (out.last_mut(), seg) {
        (Some(Segment::Text(a)), Segment::Text(b)) => a.push_str(&b),
        (Some(Segment::Unverified(a)), Segment::Unverified(b)) => a.push_str(&b),
        (_, seg) => out.push(seg),
    }
}

/// "5.0" in the answer matches "5" in the evidence.
fn matches_without_trailing_zero(n: &str, ev: &HashSet<String>) -> bool {
    n.strip_suffix(".0").is_some_and(|x| ev.contains(x))
}

enum Piece {
    Text(String),
    Cite(u32),
}

/// The Wikidata facts and COMPUTED lines have no source number, but a model may cite them anyway ("[W]").
fn is_fact_label(s: &str) -> bool {
    ["W", "Wikidata", "COMPUTED"].iter().any(|l| s.eq_ignore_ascii_case(l))
}

/// Parse `[n]`, `[n][m]`, `[n, m]`. Invalid numbers are dropped with their brackets, and so are labels
/// of lines that have no number ("[W]", "[1, W]" keeps the 1).
fn strip_citations(s: &str, n_sources: u32) -> (String, Vec<Piece>) {
    let mut plain = String::new();
    let mut pieces = Vec::new();
    let mut cur = String::new();
    let mut rest = s;
    while let Some(open) = rest.find('[') {
        let Some(close_rel) = rest[open..].find(']') else { break };
        let inner = &rest[open + 1..open + close_rel];
        let items: Vec<&str> = inner.split(',').map(str::trim).collect();
        let nums: Option<Vec<u32>> = items.iter().filter(|x| !is_fact_label(x)).map(|x| x.parse::<u32>().ok()).collect();
        cur.push_str(&rest[..open]);
        plain.push_str(&rest[..open]);
        match nums {
            Some(ns) if ns.is_empty() => cur.truncate(cur.trim_end_matches(' ').len()),
            Some(ns) => {
                // Drop the space before a citation so "claim [1]." renders as "claim¹."
                let trimmed = cur.trim_end().len();
                cur.truncate(trimmed);
                if !cur.is_empty() {
                    pieces.push(Piece::Text(std::mem::take(&mut cur)));
                }
                for n in ns {
                    if n >= 1 && n <= n_sources {
                        pieces.push(Piece::Cite(n));
                    }
                }
            }
            None => {
                cur.push_str(&rest[open..=open + close_rel]);
                plain.push_str(&rest[open..=open + close_rel]);
            }
        }
        rest = &rest[open + close_rel + 1..];
    }
    cur.push_str(rest);
    plain.push_str(rest);
    if !cur.is_empty() {
        pieces.push(Piece::Text(cur));
    }
    (plain, pieces)
}

/// Sentences with their trailing whitespace and newlines preserved, so segments rebuild the text.
fn split_keep_ws(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = s;
    for sent in sentences(s) {
        if let Some(pos) = rest.find(sent) {
            let end = pos + sent.len();
            let ws_end = rest[end..].find(|c: char| !c.is_whitespace()).map(|x| end + x).unwrap_or(rest.len());
            out.push(rest[..ws_end].to_string());
            rest = &rest[ws_end..];
        }
    }
    if !rest.is_empty() {
        out.push(rest.to_string());
    }
    out
}

pub fn plain_text(segs: &[Segment]) -> String {
    segs.iter()
        .map(|s| match s {
            Segment::Text(t) | Segment::Unverified(t) => t.clone(),
            Segment::Cite(n) => format!("[{n}]"),
        })
        .collect()
}
