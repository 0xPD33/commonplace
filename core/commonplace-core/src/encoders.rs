//! On-device ONNX encoders: leaf-mt query embedder, the Ettin cross-encoder reranker, and the
//! extractive reader for the featured snippet.

use anyhow::{Context, Result, anyhow, ensure};
use ort::session::Session;
use ort::session::builder::GraphOptimizationLevel;
use ort::value::TensorRef;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, Once};
use tokenizers::{PaddingParams, PaddingStrategy, Tokenizer, TruncationParams, TruncationStrategy};

/// mxbai query prompt; leaf-mt is distilled from mxbai and uses the same one.
pub const QUERY_PROMPT: &str = "Represent this sentence for searching relevant passages: ";
const QUERY_MAX_TOKENS: usize = 128;
const RERANK_MAX_TOKENS: usize = 256;

static ORT_INIT: Once = Once::new();

/// Load ONNX Runtime once. `dylib` overrides the default library name/path.
pub fn init_ort(dylib: Option<&Path>) -> Result<()> {
    let mut err = None;
    ORT_INIT.call_once(|| {
        let path = dylib
            .map(Path::to_path_buf)
            .or_else(|| std::env::var_os("ORT_DYLIB_PATH").map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("libonnxruntime.so"));
        match ort::init_from(&path) {
            Ok(b) => {
                b.with_name("commonplace").commit();
            }
            Err(e) => err = Some(anyhow!("load {}: {e}", path.display())),
        }
    });
    match err {
        Some(e) => Err(e),
        None => Ok(()),
    }
}

fn session(model: &Path, threads: usize) -> Result<Session> {
    Session::builder()
        .map_err(|e| anyhow!("{e}"))?
        .with_optimization_level(GraphOptimizationLevel::Level3)
        .map_err(|e| anyhow!("{e}"))?
        .with_intra_threads(threads)
        .map_err(|e| anyhow!("{e}"))?
        .commit_from_file(model)
        .with_context(|| format!("load {}", model.display()))
}

fn tokenizer(dir: &Path) -> Result<Tokenizer> {
    Tokenizer::from_file(dir.join("tokenizer.json")).map_err(|e| anyhow!("tokenizer {}: {e}", dir.display()))
}

pub struct QueryEncoder {
    session: Mutex<Session>,
    tok: Tokenizer,
    pub dims: usize,
}

impl QueryEncoder {
    /// `model` is an ONNX file inside a leaf-mt directory that also holds `tokenizer.json`.
    pub fn load(dir: &Path, model: &str, threads: usize, dims: usize) -> Result<Self> {
        let mut tok = tokenizer(dir)?;
        tok.with_truncation(Some(TruncationParams { max_length: QUERY_MAX_TOKENS, ..Default::default() }))
            .map_err(|e| anyhow!("{e}"))?;
        tok.with_padding(None);
        Ok(Self { session: Mutex::new(session(&dir.join(model), threads)?), tok, dims })
    }

    /// Embed a query: first `dims` dims of the 1024-d output, L2-normalized.
    pub fn encode(&self, query: &str) -> Result<Vec<f32>> {
        let enc = self.tok.encode(format!("{QUERY_PROMPT}{query}"), true).map_err(|e| anyhow!("{e}"))?;
        let n = enc.get_ids().len();
        let ids: Vec<i64> = enc.get_ids().iter().map(|&x| x as i64).collect();
        let mask: Vec<i64> = enc.get_attention_mask().iter().map(|&x| x as i64).collect();
        let types = vec![0i64; n];
        let mut s = self.session.lock().unwrap();
        let out = s.run(ort::inputs![
            "input_ids" => TensorRef::from_array_view(([1usize, n], &ids[..]))?,
            "attention_mask" => TensorRef::from_array_view(([1usize, n], &mask[..]))?,
            "token_type_ids" => TensorRef::from_array_view(([1usize, n], &types[..]))?,
        ])?;
        let (_, emb) = out["sentence_embedding"].try_extract_tensor::<f32>()?;
        ensure!(emb.len() >= self.dims, "embedding has {} dims, need {}", emb.len(), self.dims);
        let mut v = emb[..self.dims].to_vec();
        let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
        v.iter_mut().for_each(|x| *x /= norm);
        Ok(v)
    }
}

/// Softmax head over `QueryEncoder::encode`, trained by `pipeline/commonplace_pipeline/heads.py`, which
/// writes the weights file. `turn()` sorts a chat message into a question, a reformat request or small
/// talk; `intent()` sorts a standalone question into lookup, explain, compare or calc.
#[derive(serde::Deserialize)]
pub struct Head {
    labels: Vec<String>,
    /// Labels whose mistakes are costly: `classify` takes one only at `min_prob` or above.
    strict: Vec<String>,
    min_prob: f32,
    w: Vec<Vec<f32>>,
    b: Vec<f32>,
}

static TURN_HEAD: std::sync::LazyLock<Head> =
    std::sync::LazyLock::new(|| serde_json::from_str(include_str!("turn_kind_head.json")).expect("turn_kind_head.json"));
static INTENT_HEAD: std::sync::LazyLock<Head> =
    std::sync::LazyLock::new(|| serde_json::from_str(include_str!("intent_head.json")).expect("intent_head.json"));

impl Head {
    pub fn turn() -> &'static Head {
        &TURN_HEAD
    }

    pub fn intent() -> &'static Head {
        &INTENT_HEAD
    }

    /// Labels with their probabilities, most likely first; empty when the embedding does not fit.
    fn ranked(&self, emb: &[f32]) -> Vec<(&str, f32)> {
        if self.w.first().is_none_or(|row| row.len() != emb.len()) {
            return vec![];
        }
        let logits: Vec<f32> = self.w.iter().zip(&self.b).map(|(row, b)| row.iter().zip(emb).map(|(w, x)| w * x).sum::<f32>() + b).collect();
        let max = logits.iter().copied().fold(f32::NEG_INFINITY, f32::max);
        let sum: f32 = logits.iter().map(|z| (z - max).exp()).sum();
        let mut out: Vec<(&str, f32)> = self.labels.iter().zip(&logits).map(|(l, z)| (l.as_str(), (z - max).exp() / sum)).collect();
        out.sort_by(|a, b| b.1.total_cmp(&a.1));
        out
    }

    /// The most likely label; the first label when the embedding does not fit.
    pub fn best(&self, emb: &[f32]) -> &str {
        self.ranked(emb).first().map_or(self.labels[0].as_str(), |x| x.0)
    }

    /// The most likely label that is not strict or reaches `min_prob`.
    pub fn classify(&self, emb: &[f32]) -> &str {
        self.ranked(emb).into_iter().find(|(l, p)| !self.strict.iter().any(|s| s == l) || *p >= self.min_prob).map_or(self.labels[0].as_str(), |x| x.0)
    }
}

/// Sentence-transformers head: CLS → Dense(256, GELU) → LayerNorm → Dense(1).
struct RerankHead {
    w1: Vec<f32>,
    ln_w: Vec<f32>,
    ln_b: Vec<f32>,
    w2: Vec<f32>,
    b2: f32,
    d: usize,
}

fn load_tensor(path: &Path, name: &str) -> Result<Vec<f32>> {
    let bytes = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let st = safetensors::SafeTensors::deserialize(&bytes)?;
    let t = st.tensor(name)?;
    ensure!(t.dtype() == safetensors::Dtype::F32, "{name}: expected f32");
    Ok(t.data().chunks_exact(4).map(|c| f32::from_le_bytes(c.try_into().unwrap())).collect())
}

/// erf with max error 1.5e-7 (Abramowitz & Stegun 7.1.26), enough for exact-GELU parity.
fn erf(x: f32) -> f32 {
    let t = 1.0 / (1.0 + 0.3275911 * x.abs());
    let y = 1.0 - (((((1.061_405_4 * t - 1.453_152_1) * t) + 1.421_413_8) * t - 0.284_496_72) * t + 0.254_829_6) * t * (-x * x).exp();
    y.copysign(x)
}

impl RerankHead {
    fn load(dir: &Path) -> Result<Self> {
        let w1 = load_tensor(&dir.join("2_Dense/model.safetensors"), "linear.weight")?;
        let d = (w1.len() as f64).sqrt() as usize;
        ensure!(d * d == w1.len(), "2_Dense must be square");
        Ok(Self {
            w1,
            ln_w: load_tensor(&dir.join("3_LayerNorm/model.safetensors"), "norm.weight")?,
            ln_b: load_tensor(&dir.join("3_LayerNorm/model.safetensors"), "norm.bias")?,
            w2: load_tensor(&dir.join("4_Dense/model.safetensors"), "linear.weight")?,
            b2: load_tensor(&dir.join("4_Dense/model.safetensors"), "linear.bias")?[0],
            d,
        })
    }

    fn score(&self, cls: &[f32]) -> f32 {
        let d = self.d;
        let mut h: Vec<f32> = (0..d)
            .map(|o| {
                let x: f32 = self.w1[o * d..(o + 1) * d].iter().zip(cls).map(|(a, b)| a * b).sum();
                0.5 * x * (1.0 + erf(x / std::f32::consts::SQRT_2))
            })
            .collect();
        let mean = h.iter().sum::<f32>() / d as f32;
        let var = h.iter().map(|x| (x - mean) * (x - mean)).sum::<f32>() / d as f32;
        let inv = 1.0 / (var + 1e-5).sqrt();
        for (i, x) in h.iter_mut().enumerate() {
            *x = (*x - mean) * inv * self.ln_w[i] + self.ln_b[i];
        }
        h.iter().zip(&self.w2).map(|(a, b)| a * b).sum::<f32>() + self.b2
    }
}

pub struct Reranker {
    session: Mutex<Session>,
    tok: Tokenizer,
    head: RerankHead,
    pub batch: usize,
}

impl Reranker {
    pub fn load(dir: &Path, model: &str, threads: usize) -> Result<Self> {
        let mut tok = tokenizer(dir)?;
        tok.with_truncation(Some(TruncationParams {
            max_length: RERANK_MAX_TOKENS,
            strategy: TruncationStrategy::OnlySecond,
            ..Default::default()
        }))
        .map_err(|e| anyhow!("{e}"))?;
        let pad_id = tok.token_to_id("[PAD]").unwrap_or(50283);
        tok.with_padding(Some(PaddingParams {
            strategy: PaddingStrategy::BatchLongest,
            pad_id,
            pad_token: "[PAD]".into(),
            ..Default::default()
        }));
        Ok(Self { session: Mutex::new(session(&dir.join(model), threads)?), tok, head: RerankHead::load(dir)?, batch: 8 })
    }

    /// Relevance logits for (query, doc) pairs, in input order.
    pub fn score(&self, query: &str, docs: &[String]) -> Result<Vec<f32>> {
        let mut scores = Vec::with_capacity(docs.len());
        for chunk in docs.chunks(self.batch) {
            let pairs: Vec<(String, String)> = chunk.iter().map(|d| (query.to_string(), d.clone())).collect();
            let encs = self.tok.encode_batch(pairs, true).map_err(|e| anyhow!("{e}"))?;
            let (b, n) = (encs.len(), encs[0].get_ids().len());
            let ids: Vec<i64> = encs.iter().flat_map(|e| e.get_ids().iter().map(|&x| x as i64)).collect();
            let mask: Vec<i64> = encs.iter().flat_map(|e| e.get_attention_mask().iter().map(|&x| x as i64)).collect();
            let mut s = self.session.lock().unwrap();
            let out = s.run(ort::inputs![
                "input_ids" => TensorRef::from_array_view(([b, n], &ids[..]))?,
                "attention_mask" => TensorRef::from_array_view(([b, n], &mask[..]))?,
            ])?;
            let (shape, hidden) = out["last_hidden_state"].try_extract_tensor::<f32>()?;
            let d = shape[2] as usize;
            ensure!(d == self.head.d, "hidden size {d} != head size {}", self.head.d);
            for i in 0..b {
                let cls = &hidden[i * n * d..i * n * d + d];
                scores.push(self.head.score(cls));
            }
        }
        Ok(scores)
    }
}

const READER_MAX_TOKENS: usize = 384;
/// Longest answer span the reader may return, in tokens.
const READER_MAX_SPAN: usize = 30;

/// A reader answer: a passage substring and its margin over "no answer" (logit units).
#[derive(Debug, Clone)]
pub struct Span {
    pub text: String,
    pub margin: f32,
}

/// SQuAD2 extractive reader (PLAN.md §9.3a): the best answer span in a passage, or none.
pub struct Reader {
    session: Mutex<Session>,
    tok: Tokenizer,
    types: bool,
}

impl Reader {
    pub fn load(dir: &Path, model: &str, threads: usize) -> Result<Self> {
        let mut tok = tokenizer(dir)?;
        tok.with_truncation(Some(TruncationParams {
            max_length: READER_MAX_TOKENS,
            strategy: TruncationStrategy::OnlySecond,
            ..Default::default()
        }))
        .map_err(|e| anyhow!("{e}"))?;
        tok.with_padding(None);
        let session = session(&dir.join(model), threads)?;
        let types = session.inputs().iter().any(|i| i.name() == "token_type_ids");
        Ok(Self { session: Mutex::new(session), tok, types })
    }

    /// The best span of `passage` answering `question`, if it beats the "no answer" score.
    pub fn read(&self, question: &str, passage: &str) -> Result<Option<Span>> {
        let enc = self.tok.encode((question, passage), true).map_err(|e| anyhow!("{e}"))?;
        let n = enc.get_ids().len();
        let ids: Vec<i64> = enc.get_ids().iter().map(|&x| x as i64).collect();
        let mask: Vec<i64> = enc.get_attention_mask().iter().map(|&x| x as i64).collect();
        let types: Vec<i64> = enc.get_type_ids().iter().map(|&x| x as i64).collect();
        let mut s = self.session.lock().unwrap();
        let out = if self.types {
            s.run(ort::inputs![
                "input_ids" => TensorRef::from_array_view(([1usize, n], &ids[..]))?,
                "attention_mask" => TensorRef::from_array_view(([1usize, n], &mask[..]))?,
                "token_type_ids" => TensorRef::from_array_view(([1usize, n], &types[..]))?,
            ])?
        } else {
            s.run(ort::inputs![
                "input_ids" => TensorRef::from_array_view(([1usize, n], &ids[..]))?,
                "attention_mask" => TensorRef::from_array_view(([1usize, n], &mask[..]))?,
            ])?
        };
        let (_, start) = out["start_logits"].try_extract_tensor::<f32>()?;
        let (_, end) = out["end_logits"].try_extract_tensor::<f32>()?;
        // SQuAD2: the "no answer" score sits on the first token.
        let null = start[0] + end[0];
        let in_passage: Vec<bool> = enc.get_sequence_ids().iter().map(|s| *s == Some(1)).collect();
        let mut best: Option<(f32, usize, usize)> = None;
        for i in (0..n).filter(|&i| in_passage[i]) {
            for j in (i..(i + READER_MAX_SPAN).min(n)).take_while(|&j| in_passage[j]) {
                let sc = start[i] + end[j];
                if best.is_none_or(|b| sc > b.0) {
                    best = Some((sc, i, j));
                }
            }
        }
        let Some((sc, i, j)) = best.filter(|b| b.0 > null) else { return Ok(None) };
        let (a, b) = (enc.get_offsets()[i].0, enc.get_offsets()[j].1);
        let text = passage.get(a..b).unwrap_or_default().trim().to_string();
        Ok((!text.is_empty()).then(|| Span { text, margin: sc - null }))
    }
}
