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
    pub deep: bool,
    /// Thinking mode was on (reasoning tokens are in `synthesis.think_tokens`).
    pub think: bool,
    pub complex: bool,
    /// The intent head's best guess for the searched question: lookup, explain, compare or calc.
    pub intent: String,
    pub planner_used: bool,
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
    pub planner: Option<GenStats>,
    pub compute: Option<GenStats>,
    pub synthesis: Option<GenStats>,
    pub evidence_tokens: usize,
    pub n_sources: usize,
    pub unverified_sentences: usize,
    pub peak_rss_mb: f64,
    pub thermal_headroom: Option<f32>,
    pub threads: Option<u32>,
    pub model_id: Option<String>,
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

pub fn append_jsonl(path: &Path, rec: &QueryRecord) -> std::io::Result<()> {
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(path)?;
    let line = serde_json::to_string(rec).map_err(std::io::Error::other)?;
    writeln!(f, "{line}")
}
