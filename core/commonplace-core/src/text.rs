//! Small text helpers shared by routing, evidence selection and the citation check.

use regex::Regex;
use std::borrow::Cow;
use std::collections::HashSet;
use std::sync::LazyLock;

pub static STOPWORDS: LazyLock<HashSet<&'static str>> = LazyLock::new(|| {
    "a an and are as at be but by can could did do does for from had has have how i if in into is it its \
     me my of on or our so than that the their them then there these they this those to was we were what \
     when where which who whom why will with would you your about tell explain describe much many"
        .split_whitespace()
        .collect()
});

static REF_MARKS: LazyLock<Regex> = LazyLock::new(|| {
    let mark = r"\[(?:[1-9]\d{0,2}|(?:note|nb) \d{1,3}|citation needed|[a-z])\]";
    Regex::new(&format!("{mark}(?: ?{mark})*")).unwrap()
});
static CODE_LINE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[={}<>\\]|\w\(").unwrap());

/// Removes wiki reference marks: `year[1]`, `orbit.[2][3]`, `end. [1] Next`, `[citation needed]`, `[note 4]`,
/// `[nb 2]` and `[a]` after punctuation. Other brackets stay: `[sic]`, `[...]`, a list number that starts a line,
/// a mark that is not followed by whitespace, and any mark on a line that looks like code (`x = a[1]`).
pub fn strip_ref_marks(text: &str) -> Cow<'_, str> {
    if !text.contains('[') {
        return Cow::Borrowed(text);
    }
    let mut out = String::with_capacity(text.len());
    for line in text.split_inclusive('\n') {
        let mut last = 0;
        if !CODE_LINE.is_match(line) {
            for m in REF_MARKS.find_iter(line) {
                let (head, tail) = (&line[..m.start()], &line[m.end()..]);
                let before = head.trim_end_matches(' ');
                let letter = m.as_str().as_bytes()[2] == b']' && m.as_str().as_bytes()[1].is_ascii_lowercase();
                let ok = match before.chars().next_back() {
                    None => tail.trim().is_empty(),
                    Some(c) if before.len() < head.len() => !letter && ".,;:!?)\"”’".contains(c),
                    Some(c) => ".,;:!?)\"'”’".contains(c) || (!letter && (c.is_alphanumeric() || c == '%')),
                };
                if ok && tail.chars().next().is_none_or(char::is_whitespace) {
                    out.push_str(&line[last..before.len()]);
                    last = m.end();
                }
            }
        }
        out.push_str(&line[last..]);
    }
    if out.len() == text.len() { Cow::Borrowed(text) } else { Cow::Owned(out) }
}

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
