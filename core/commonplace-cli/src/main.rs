//! Desktop CLI over the same core as the app: ask, retrieve, bench, serve-eval, info.

use anyhow::{Context, Result};
use clap::{Args, Parser, Subcommand};
use commonplace_core::citations::{Segment, plain_text};
use commonplace_core::engine::{AskOutcome, AskRequest, Engine, EngineConfig, Event, EventSink, Stage, Turn};
use commonplace_core::llm::LlmBackend;
use commonplace_core::pack::ModelRole;
use commonplace_llm::{LlamaBackend, LoadOptions};
use serde::Deserialize;
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Parser)]
#[command(name = "commonplace", about = "Offline research assistant (desktop CLI)")]
struct Cli {
    #[command(flatten)]
    opts: Opts,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Args, Clone)]
struct Opts {
    /// Library directory holding packs/.
    #[arg(long, global = true, default_value = "data/library")]
    library: PathBuf,
    #[arg(long, global = true, default_value = "data/models/leaf-mt")]
    leaf_dir: PathBuf,
    #[arg(long, global = true, default_value = "onnx/model.onnx")]
    leaf_model: String,
    #[arg(long, global = true, default_value = "data/models/ettin-17m")]
    ettin_dir: PathBuf,
    #[arg(long, global = true, default_value = "onnx/model.onnx")]
    ettin_model: String,
    /// Extractive reader for the featured snippet (skipped when the directory is missing).
    #[arg(long, global = true, default_value = "data/models/reader")]
    reader_dir: PathBuf,
    #[arg(long, global = true, default_value = "onnx/model_int8.onnx")]
    reader_model: String,
    /// GGUF to load directly (otherwise the library's fast model pack, if any).
    #[arg(long, global = true)]
    model: Option<PathBuf>,
    /// Retrieval only: no LLM.
    #[arg(long, global = true)]
    no_llm: bool,
    #[arg(long, global = true, default_value_t = 8)]
    threads: u32,
    #[arg(long, global = true)]
    no_dense: bool,
    #[arg(long, global = true)]
    no_sparse: bool,
    #[arg(long, global = true)]
    no_rerank: bool,
    #[arg(long, global = true)]
    no_cards: bool,
    #[arg(long, global = true)]
    no_planner: bool,
    /// Let the model rewrite the message into a standalone, typo-free search query first (off by default).
    #[arg(long, global = true)]
    rewrite: bool,
    /// Search the raw message (the default; kept for older scripts).
    #[arg(long, global = true)]
    no_rewrite: bool,
    #[arg(long, global = true)]
    no_doc2query: bool,
    #[arg(long, global = true)]
    telemetry: Option<PathBuf>,
    /// Retrieval tuning (defaults in RetrievalSettings): fused pool size before the rerank.
    #[arg(long, global = true)]
    fuse_keep: Option<usize>,
    #[arg(long, global = true)]
    w_entity: Option<f32>,
    #[arg(long, global = true)]
    prior_weight: Option<f32>,
    #[arg(long, global = true)]
    nprobe: Option<usize>,
    #[arg(long, global = true)]
    keyword_only_keep: Option<usize>,
    /// Weight of the fusion rank added to the rerank score (0 = reranker only).
    #[arg(long, global = true)]
    rerank_blend: Option<f32>,
    /// Reasoning-token cap for thinking mode.
    #[arg(long, global = true)]
    think_budget: Option<u32>,
    /// Answer-token cap (the app's "Answer length" setting; default 400).
    #[arg(long, global = true)]
    max_answer_tokens: Option<u32>,
    /// Drop evidence passages scoring more than this below the best rerank score.
    #[arg(long, global = true)]
    evidence_gap: Option<f32>,
    /// Evidence token budget (phone default 600, desktop 900).
    #[arg(long, global = true)]
    evidence_tokens: Option<usize>,
}

#[derive(Subcommand)]
enum Cmd {
    /// Answer a question.
    Ask {
        query: String,
        #[arg(long)]
        json: bool,
        #[arg(long)]
        deep: bool,
        /// Let the model reason first (capped by --think-budget).
        #[arg(long)]
        think: bool,
    },
    /// Show the reranked retrieval results only.
    Retrieve {
        query: String,
        #[arg(long, default_value_t = 10)]
        k: usize,
        /// Print JSON (one object with a "hits" list) instead of text.
        #[arg(long)]
        json: bool,
    },
    /// Run a list of queries (one per line or JSONL with "query") and print a timing table.
    Bench { queries: PathBuf },
    /// Read JSON requests on stdin ({"query", "history"?, "deep"?}); write one JSON result per line.
    ServeEval,
    /// Read JSONL {"id", "query"} on stdin; write each query's fused pool (reranker input text, fusion
    /// order, reranker score) as one JSON line. Reranker training data.
    Pool,
    /// Score (query, doc) pairs with the reranker.
    Rerank { query: String, docs: Vec<String> },
    /// Embed each stdin line with the query encoder, as the engine does; write one JSON array per line.
    /// Training data for the turn-kind head (pipeline `turn_kind`).
    Encode {
        /// Leading embedding dimensions kept; the library's embedder uses 512.
        #[arg(long, default_value_t = 512)]
        dims: usize,
    },
    /// Library, model and CPU info.
    Info,
}

fn engine(o: &Opts) -> Result<Engine> {
    let threads = o.threads;
    let cfg = EngineConfig {
        library_dir: o.library.clone(),
        leaf_dir: o.leaf_dir.exists().then(|| o.leaf_dir.clone()),
        leaf_model: o.leaf_model.clone(),
        ettin_dir: o.ettin_dir.exists().then(|| o.ettin_dir.clone()),
        ettin_model: o.ettin_model.clone(),
        reader_dir: o.reader_dir.join(&o.reader_model).exists().then(|| o.reader_dir.clone()),
        reader_model: o.reader_model.clone(),
        ort_dylib: None,
        encoder_threads: 4,
        telemetry_path: o.telemetry.clone(),
    };
    let loader: commonplace_core::engine::LlmLoader = Box::new(move |mp, t| {
        let file = mp.file().context("model pack has no file")?;
        let n_ctx = mp.manifest.model.as_ref().map(|m| m.n_ctx).unwrap_or(4096);
        let llm = LlamaBackend::load(&file, &LoadOptions { n_ctx, threads: t.max(threads), batch_threads: t.max(threads), cpus: vec![], in_ram: true })?;
        Ok(Arc::new(llm) as Arc<dyn LlmBackend>)
    });
    let e = Engine::new(cfg, Some(loader))?;
    {
        let mut s = e.settings.write().unwrap();
        s.llm_threads = threads;
        s.retrieval.use_dense = !o.no_dense;
        s.retrieval.use_sparse = !o.no_sparse;
        s.retrieval.use_rerank = !o.no_rerank;
        s.use_cards = !o.no_cards;
        s.use_planner = !o.no_planner;
        s.rewrite = o.rewrite && !o.no_rewrite;
        let r = &mut s.retrieval;
        r.fuse_keep = o.fuse_keep.unwrap_or(r.fuse_keep);
        r.w_entity = o.w_entity.unwrap_or(r.w_entity);
        r.prior_weight = o.prior_weight.unwrap_or(r.prior_weight);
        r.nprobe = o.nprobe.unwrap_or(r.nprobe);
        r.keyword_only_keep = o.keyword_only_keep.unwrap_or(r.keyword_only_keep);
        r.rerank_blend = o.rerank_blend.unwrap_or(r.rerank_blend);
        s.think_budget = o.think_budget.unwrap_or(s.think_budget);
        s.max_answer_tokens = o.max_answer_tokens.unwrap_or(s.max_answer_tokens);
        s.evidence_score_gap = o.evidence_gap.or(s.evidence_score_gap);
        s.evidence_tokens = o.evidence_tokens.unwrap_or(s.evidence_tokens);
    }
    if o.no_doc2query {
        eprintln!("note: --no-doc2query needs a pack built without questions; flag recorded only");
    }
    if !o.no_llm {
        if let Some(path) = &o.model {
            let llm = LlamaBackend::load(path, &LoadOptions { n_ctx: 8192, threads, batch_threads: threads, cpus: vec![], in_ram: true })?;
            Engine::warm_up(&llm, &e.library().snapshot_date());
            e.set_llm(ModelRole::LlmFast, Some(Arc::new(llm)));
        } else if e.library().model(&ModelRole::LlmFast).is_some() {
            e.load_model(ModelRole::LlmFast)?;
        } else if e.library().model(&ModelRole::LlmSmall).is_some() {
            e.load_model(ModelRole::LlmSmall)?;
        }
    }
    let lib = e.library();
    for (p, why) in &lib.skipped {
        eprintln!("skipped pack {p}: {why}");
    }
    Ok(e)
}

/// Streams the answer to the terminal: new text only, citations as [n].
struct TermSink {
    printed: AtomicUsize,
    quiet: bool,
}

impl EventSink for TermSink {
    fn event(&self, e: Event) {
        if self.quiet {
            return;
        }
        match e {
            Event::Stage { stage, detail } if stage != Stage::Done => eprintln!("· {stage:?} {detail}"),
            Event::Card(c) => {
                eprintln!("── card ({:.0} ms) ──", c.card_ms);
                if let Some(t) = &c.top {
                    eprintln!("{} — {}\n  » {}", t.source.title, t.source.section, t.highlight);
                }
                if let Some(f) = &c.featured {
                    eprintln!("  ★ {} ({:.1}) — {}", f.text, f.margin, f.source.title);
                }
                for f in &c.facts {
                    eprintln!("  [W] {}", f.line());
                }
                for x in &c.computed {
                    eprintln!("  = {x}");
                }
                for s in c.sources.iter().take(8) {
                    eprintln!("  · {} — {} ({}) {:.2}", s.title, s.section, s.pack_id, s.score);
                }
                eprintln!("──");
            }
            Event::Thinking { text, done } => {
                if done {
                    eprintln!("── reasoning ──\n{text}\n──");
                }
            }
            Event::Answer { raw: text, done, .. } => {
                let n = self.printed.load(Ordering::Relaxed);
                if text.len() > n && text.is_char_boundary(n) {
                    print!("{}", &text[n..]);
                    let _ = std::io::stdout().flush();
                    self.printed.store(text.len(), Ordering::Relaxed);
                }
                if done {
                    println!();
                }
            }
            _ => {}
        }
    }
}

fn outcome_json(o: &AskOutcome) -> serde_json::Value {
    serde_json::json!({
        "kind": o.kind,
        "answer": plain_text(&o.segments),
        "segments": o.segments,
        "sources": o.sources,
        "card": o.card,
        "record": o.record,
    })
}

fn print_summary(o: &AskOutcome) {
    let unverified: Vec<&String> = o.segments.iter().filter_map(|s| if let Segment::Unverified(t) = s { Some(t) } else { None }).collect();
    if !unverified.is_empty() {
        eprintln!("\n⚠ unverified numbers in: {unverified:?}");
    }
    eprintln!("\nSources:");
    for s in &o.sources {
        eprintln!("  [{}] {} — {} ({})", s.n, s.title, s.section, s.pack_id);
    }
    let r = &o.record;
    let syn = r.synthesis.as_ref();
    eprintln!(
        "\ncard {:.0} ms · ttft {} · total {:.0} ms · prefill {} · decode {} · evidence ~{} tok · rss {:.0} MB",
        r.card_ms,
        r.ttft_ms.map(|x| format!("{x:.0} ms")).unwrap_or("-".into()),
        r.total_ms,
        syn.map(|s| format!("{:.0} tok/s ({} cached)", s.prefill_tps(), s.cached_tokens)).unwrap_or("-".into()),
        syn.map(|s| format!("{:.1} tok/s", s.decode_tps())).unwrap_or("-".into()),
        r.evidence_tokens,
        r.peak_rss_mb
    );
}

#[derive(Deserialize)]
struct EvalReq {
    query: String,
    #[serde(default)]
    history: Vec<Turn>,
    #[serde(default)]
    deep: bool,
    #[serde(default)]
    think: bool,
}

fn pct(v: &mut [f64], p: f64) -> f64 {
    if v.is_empty() {
        return 0.0;
    }
    v.sort_by(|a, b| a.total_cmp(b));
    v[((v.len() - 1) as f64 * p).round() as usize]
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let o = &cli.opts;
    match &cli.cmd {
        Cmd::Rerank { query, docs } => {
            commonplace_core::encoders::init_ort(None)?;
            let rr = commonplace_core::encoders::Reranker::load(&o.ettin_dir, &o.ettin_model, 4)?;
            for (d, s) in docs.iter().zip(rr.score(query, docs)?) {
                println!("{s:>8.3}  {d}");
            }
        }
        Cmd::Encode { dims } => {
            commonplace_core::encoders::init_ort(None)?;
            let enc = commonplace_core::encoders::QueryEncoder::load(&o.leaf_dir, &o.leaf_model, 4, *dims)?;
            for line in std::io::stdin().lock().lines() {
                println!("{}", serde_json::to_string(&enc.encode(&line?)?)?);
            }
        }
        Cmd::Info => {
            println!("{}", commonplace_llm::system_info());
            let e = engine(&Opts { no_llm: true, ..o.clone() })?;
            let lib = e.library();
            for p in &lib.packs {
                let m = &p.manifest;
                println!(
                    "pack {} — {} (snapshot {}) passages={} dense={} sparse={} cards={}",
                    m.pack_id,
                    m.title,
                    m.snapshot_date,
                    m.counts.as_ref().map(|c| c.passages).unwrap_or(0),
                    p.dense.is_some(),
                    p.sparse.is_some(),
                    p.cards.is_some()
                );
            }
            for m in &lib.models {
                println!("model {} {:?}", m.manifest.pack_id, m.role());
            }
            if let Some((m, _)) = &lib.wikidata {
                println!("wikidata {}", m.snapshot_date);
            }
            println!("total {:.2} GB", lib.total_bytes() as f64 / 1e9);
        }
        Cmd::Retrieve { query, k, json } => {
            let e = engine(&Opts { no_llm: true, ..o.clone() })?;
            {
                let mut s = e.settings.write().unwrap();
                s.retrieval.rerank_keep = *k;
                s.retrieval.fuse_keep = s.retrieval.fuse_keep.max(*k);
            }
            let out = e.ask(&AskRequest { query: query.clone(), ..Default::default() }, &TermSink { printed: 0.into(), quiet: true })?;
            if *json {
                println!("{}", serde_json::json!({ "query": query, "hits": out.card.sources, "retrieval": out.record.retrieval, "card_ms": out.record.card_ms }));
                return Ok(());
            }
            for s in &out.card.sources {
                println!("{:>7.3}  {} — {} [{}:{}]\n         {}", s.score, s.title, s.section, s.pack_id, s.passage_id, s.snippet);
            }
            let r = &out.record.retrieval;
            eprintln!(
                "encode {:.0} · sparse {:.0} · dense {:.0} · fuse {:.0} · rerank {:.0} ms · card {:.0} ms",
                r.encode_ms, r.sparse_ms, r.dense_ms, r.fuse_ms, r.rerank_ms, out.record.card_ms
            );
        }
        Cmd::Ask { query, json, deep, think } => {
            let e = engine(&Opts { no_llm: o.no_llm || *deep, ..o.clone() })?;
            if *deep && !o.no_llm {
                e.load_model(ModelRole::LlmDeep)?;
            }
            let sink = TermSink { printed: 0.into(), quiet: *json };
            let out = e.ask(&AskRequest { query: query.clone(), history: vec![], deep: *deep, think: *think }, &sink)?;
            if *json {
                println!("{}", outcome_json(&out));
            } else {
                print_summary(&out);
            }
        }
        Cmd::ServeEval => {
            let e = engine(o)?;
            let stdin = std::io::stdin();
            for line in stdin.lock().lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                let req: EvalReq = serde_json::from_str(&line)?;
                let sink = TermSink { printed: 0.into(), quiet: true };
                let v = match e.ask(&AskRequest { query: req.query, history: req.history, deep: req.deep, think: req.think }, &sink) {
                    Ok(out) => outcome_json(&out),
                    Err(err) => serde_json::json!({ "error": format!("{err:#}") }),
                };
                println!("{v}");
                std::io::stdout().flush()?;
            }
        }
        Cmd::Pool => {
            let e = engine(&Opts { no_llm: true, ..o.clone() })?;
            for line in std::io::stdin().lock().lines() {
                let req: serde_json::Value = serde_json::from_str(&line?)?;
                let (lib, hits) = e.pool(req["query"].as_str().context("query")?)?;
                let hits: Vec<_> = hits
                    .iter()
                    .map(|h| {
                        serde_json::json!({ "pack": lib.packs[h.pack as usize].manifest.pack_id, "pid": h.pid,
                            "title": h.article.title, "text": h.rerank_text(), "fused": h.fused, "rerank": h.rerank })
                    })
                    .collect();
                println!("{}", serde_json::json!({ "id": req["id"], "query": req["query"], "hits": hits }));
            }
        }
        Cmd::Bench { queries } => {
            let e = engine(o)?;
            let text = std::fs::read_to_string(queries)?;
            let qs: Vec<String> = text
                .lines()
                .filter(|l| !l.trim().is_empty())
                .map(|l| serde_json::from_str::<serde_json::Value>(l).ok().and_then(|v| v["query"].as_str().map(String::from)).unwrap_or(l.to_string()))
                .collect();
            let (mut card, mut ttft, mut total, mut dec) = (vec![], vec![], vec![], vec![]);
            for q in &qs {
                let out = e.ask(&AskRequest { query: q.clone(), ..Default::default() }, &TermSink { printed: 0.into(), quiet: true })?;
                let r = &out.record;
                card.push(r.card_ms);
                total.push(r.total_ms);
                if let Some(t) = r.ttft_ms {
                    ttft.push(t);
                }
                if let Some(s) = &r.synthesis {
                    dec.push(s.decode_tps());
                }
                eprintln!("{:>7.0} {:>7.0} {:>7.0}  {}", r.card_ms, r.ttft_ms.unwrap_or(0.0), r.total_ms, q);
            }
            println!("queries: {}", qs.len());
            println!("card ms   p50 {:>7.0}  p90 {:>7.0}", pct(&mut card.clone(), 0.5), pct(&mut card, 0.9));
            println!("ttft ms   p50 {:>7.0}  p90 {:>7.0}", pct(&mut ttft.clone(), 0.5), pct(&mut ttft, 0.9));
            println!("total ms  p50 {:>7.0}  p90 {:>7.0}", pct(&mut total.clone(), 0.5), pct(&mut total, 0.9));
            println!("decode tok/s p50 {:>5.1}", pct(&mut dec, 0.5));
        }
    }
    Ok(())
}
