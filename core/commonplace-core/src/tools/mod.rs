pub mod calc;
pub mod units;
pub mod wikidata;

/// The words before a calculation in a question that asks for nothing else: "What is", "Convert".
pub(crate) fn bare(prefix: &str) -> bool {
    let filler = ["what", "what's", "whats", "is", "convert", "calculate", "compute", "how", "much", "please", "tell", "me"];
    prefix.split(|c: char| !c.is_alphanumeric() && c != '\'').filter(|w| !w.is_empty()).all(|w| filler.contains(&w.to_lowercase().as_str()))
}
