pub mod calc;
pub mod units;
pub mod wikidata;

/// A number as a question writes it: thousands in groups of three digits, or a decimal comma ("1,5").
pub(crate) const NUM: &str = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+,\d{1,2}|\d+(?:\.\d+)?)";

/// The value of a `NUM` match, with an optional minus sign. A comma before one or two digits can only be a decimal comma.
pub(crate) fn parse_num(s: &str) -> Option<f64> {
    match s.rsplit_once(',') {
        Some((whole, tail)) if tail.len() < 3 => format!("{whole}.{tail}").parse().ok(),
        _ => s.replace(',', "").parse().ok(),
    }
}

/// Whether the number that follows `before` is only part of a larger quantity: "5 ft 10 in", "5-10 miles", "10-15%".
pub(crate) fn part_of_larger(before: &str, num: &str) -> bool {
    before.contains(|c: char| c.is_ascii_digit()) || before.ends_with(['-', '\u{2013}']) || (num.starts_with('-') && !before.is_empty() && !before.ends_with(char::is_whitespace))
}

/// The words before a calculation in a question that asks for nothing else: "What is", "Convert".
pub(crate) fn bare(prefix: &str) -> bool {
    let filler = ["what", "what's", "whats", "is", "convert", "calculate", "compute", "how", "much", "please", "tell", "me"];
    prefix.split(|c: char| !c.is_alphanumeric() && c != '\'').filter(|w| !w.is_empty()).all(|w| filler.contains(&w.to_lowercase().as_str()))
}
