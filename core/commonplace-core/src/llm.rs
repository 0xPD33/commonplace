//! LLM backend interface. Implemented by llama.cpp (commonplace-llm) and by LiteRT-LM on the
//! Kotlin side (through a UniFFI foreign trait).

use anyhow::Result;
use serde::Serialize;

#[derive(Debug, Clone)]
pub struct GenParams {
    pub max_tokens: u32,
    pub temperature: f32,
    pub top_p: f32,
    pub repeat_penalty: f32,
    /// GBNF grammar. Backends without grammar support ignore it; callers parse tolerantly.
    pub grammar: Option<String>,
    /// Thinking mode: at most this many reasoning tokens, streamed as `<think>…</think>` before the
    /// answer. `None` switches thinking off. Backends without a thinking mode ignore it.
    pub think_budget: Option<u32>,
}

impl GenParams {
    pub fn synthesis(max_tokens: u32) -> Self {
        Self { max_tokens, temperature: 0.3, top_p: 0.9, repeat_penalty: 1.05, grammar: None, think_budget: None }
    }

    /// Short deterministic output without a grammar (query rewriting).
    pub fn greedy(max_tokens: u32) -> Self {
        Self { max_tokens, temperature: 0.0, top_p: 1.0, repeat_penalty: 1.0, grammar: None, think_budget: None }
    }

    pub fn json(max_tokens: u32, grammar: &str) -> Self {
        Self { max_tokens, temperature: 0.0, top_p: 1.0, repeat_penalty: 1.0, grammar: Some(grammar.to_string()), think_budget: None }
    }
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct GenStats {
    pub prompt_tokens: u32,
    pub cached_tokens: u32,
    pub gen_tokens: u32,
    /// Reasoning tokens among `gen_tokens` (thinking mode).
    pub think_tokens: u32,
    /// The answer stopped at `max_tokens`, not at the model's end token.
    pub truncated: bool,
    pub prefill_ms: f64,
    pub decode_ms: f64,
}

impl GenStats {
    pub fn prefill_tps(&self) -> f64 {
        let n = self.prompt_tokens.saturating_sub(self.cached_tokens) as f64;
        if self.prefill_ms > 0.0 { n / (self.prefill_ms / 1000.0) } else { 0.0 }
    }
    pub fn decode_tps(&self) -> f64 {
        if self.decode_ms > 0.0 { self.gen_tokens as f64 / (self.decode_ms / 1000.0) } else { 0.0 }
    }
}

pub trait LlmBackend: Send + Sync {
    fn id(&self) -> String;
    /// Generate a reply to one user turn. `on_token` returns false to stop early.
    /// Backends cache the prefill of `system` and reuse it when the same system text comes back.
    fn generate(&self, system: &str, user: &str, params: &GenParams, on_token: &mut dyn FnMut(&str) -> bool) -> Result<GenStats>;
    /// Change the thread count (thermal adaptation). Default: unsupported, ignored.
    fn set_threads(&self, _n: u32) {}
}
