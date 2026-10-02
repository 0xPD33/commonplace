//! Small text helpers shared by routing, evidence selection and the citation check.

use std::collections::HashSet;
use std::sync::LazyLock;

pub static STOPWORDS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    "a an and are as at be but by can could did do does for from had has have how i if in into is it its \
     me my of on or our so than that the their them then there these they this those to was we were what \
     when where which who whom why will with would you your about tell explain describe much many"
        .split_whitespace()
        .collect()
});

/// Lowercased content words, crudely stemmed to 6 characters for overlap scoring.
pub fn terms(s: &str) -> Vec<String> {
    s.split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty())
        .map(|w| w.to_lowercase())
        .filter(|w| !STOPWORDS.contains(w.as_str()))
        .map(|w| w.chars().take(6).collect())
        .collect()
}

/// Sentence split on terminal punctuation followed by space and an uppercase letter, digit or quote.
pub fn sentences(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let b = text.as_bytes();
    let mut start = 0;
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        if c == b'\n' || ((c == b'.' || c == b'!' || c == b'?') && i + 2 < b.len() && b[i + 1] == b' ') {
            let next = if c == b'\n' { b.get(i + 1).copied() } else { b.get(i + 2).copied() };
            let boundary = c == b'\n' || next.is_some_and(|n| n.is_ascii_uppercase() || n.is_ascii_digit() || n == b'"' || n == b'(');
            // Skip common abbreviations like "U.S." or "Dr." (a single capital or a short title before the dot).
            let prev_word = text[start..i].rsplit(' ').next().unwrap_or("");
            let abbrev = c == b'.' && (prev_word.len() <= 2 && prev_word.chars().all(|x| x.is_ascii_uppercase() || x == '.'));
            if boundary && !abbrev {
                let s = text[start..=i].trim();
                if !s.is_empty() {
                    out.push(s);
                }
                start = i + 1;
            }
        }
        i += 1;
    }
    let s = text[start..].trim();
    if !s.is_empty() {
        out.push(s);
    }
    out
}

/// Rough token estimate (≈4 characters per token for English).
pub fn approx_tokens(s: &str) -> usize {
    s.len().div_ceil(4)
}

/// Numbers written with digits, normalized: commas removed, trailing ".0" kept as written.
/// Single-digit numbers are ignored; they are usually counts or list markers.
pub fn numbers(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let chars: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i].is_ascii_digit() && (i == 0 || !chars[i - 1].is_alphanumeric()) {
            let mut j = i;
            let mut num = String::new();
            while j < chars.len() {
                let c = chars[j];
                if c.is_ascii_digit() {
                    num.push(c);
                } else if (c == ',' || c == '.') && j + 1 < chars.len() && chars[j + 1].is_ascii_digit() {
                    if c == '.' {
                        num.push('.');
                    }
                } else {
                    break;
                }
                j += 1;
            }
            if num.len() >= 2 || num.contains('.') {
                out.push(num);
            }
            i = j;
        } else {
            i += 1;
        }
    }
    out
}
