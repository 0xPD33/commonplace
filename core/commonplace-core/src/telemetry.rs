//! Per-query telemetry: stage timings, counts, token rates, RSS, thermal.

use crate::llm::GenStats;
use crate::retrieval::RetrievalStats;
use serde::Serialize;
use std::io::Write;
use std::path::Path;

pub const BUILD: &str = env!("CP_BUILD");

#[derive(Debug, Clone, Default, Serialize)]
pub struct QueryRecord {
    pub ts_ms: u64,
    pub query: String,
    /// What was searched: the rewritten question (or the raw one) plus any follow-up context.
    pub search_query: String,
    /// Article titles the rewrite model named as the question's topics.
    pub topics: Vec<String>,
    /// Thinking mode was on (reasoning tokens are in `synthesis.think_tokens`).
    pub think: bool,
    pub complex: bool,
    /// The intent head's best guess for the searched question: lookup, explain, compare or calc.
    pub intent: String,
    pub subqueries: Vec<String>,
    /// (stage, ms since the query started)
    pub stages: Vec<(String, f64)>,
    pub card_ms: f64,
    /// Extractive reader time and the shown span's margin (featured snippet).
    pub reader_ms: Option<f64>,
    pub featured_margin: Option<f32>,
    pub ttft_ms: Option<f64>,
    pub total_ms: f64,
    pub retrieval: RetrievalStats,
    /// The query-rewrite call (thinking-free, one line).
    pub rewrite: Option<GenStats>,
    pub compute: Option<GenStats>,
    pub synthesis: Option<GenStats>,
    pub evidence_tokens: usize,
    /// The evidence text the model read. Serve-eval prints it; telemetry files do not carry it.
    #[serde(skip)]
    pub evidence: String,
    pub n_sources: usize,
    pub unverified_sentences: usize,
    pub peak_rss_mb: f64,
    pub thermal_headroom: Option<f32>,
    pub threads: Option<u32>,
    pub model_id: Option<String>,
    /// The model's defaults (`profiles::ModelProfile::name`).
    pub profile: String,
    pub build: String,
    pub error: Option<String>,
}

pub fn now_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// Peak resident set size (VmHWM) in MB; 0 where /proc is unavailable.
pub fn peak_rss_mb() -> f64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|s| {
            s.lines()
                .find(|l| l.starts_with("VmHWM:"))
                .and_then(|l| l.split_whitespace().nth(1))
                .and_then(|kb| kb.parse::<f64>().ok())
        })
        .map(|kb| kb / 1024.0)
        .unwrap_or(0.0)
}

/// Append one record. Without `with_text`, the line holds the timings and no words of the user's.
pub fn append_jsonl(path: &Path, rec: &QueryRecord, with_text: bool) -> std::io::Result<()> {
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path)?;
    let line = if with_text {
        serde_json::to_string(rec)
    } else {
        serde_json::to_string(&QueryRecord { query: String::new(), search_query: String::new(), topics: vec![], subqueries: vec![], ..rec.clone() })
    }
    .map_err(std::io::Error::other)?;
    writeln!(f, "{line}")
}

/// Largest telemetry file that a release build keeps: about 700 questions.
const TELEMETRY_MAX_BYTES: u64 = 1 << 20;

/// For a build that keeps no question text: delete a file that older builds filled with questions, or one that has grown too large.
pub fn prune(path: &Path) {
    let Ok(f) = std::fs::File::open(path) else { return };
    let oversized = f.metadata().is_ok_and(|m| m.len() > TELEMETRY_MAX_BYTES);
    let mut first = String::new();
    let _ = std::io::BufRead::read_line(&mut std::io::BufReader::new(f), &mut first);
    let has_text = serde_json::from_str::<serde_json::Value>(&first).is_ok_and(|v| v["query"].as_str().is_some_and(|q| !q.is_empty()));
    if oversized || has_text {
        let _ = std::fs::remove_file(path);
    }
}
