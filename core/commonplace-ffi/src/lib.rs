//! UniFFI surface for the Android app. Calls block; Kotlin runs them on a background dispatcher.

use commonplace_core::citations::Segment;
use commonplace_core::engine::{self, AskRequest, Engine, EngineConfig, Event, EventSink, LlmLoader, Stage};
use commonplace_core::library::{FOOTPRINT_CAP, Library};
use commonplace_core::llm::{GenParams, GenStats, LlmBackend};
use commonplace_core::pack::{self, ModelRole, PackType, import};
use commonplace_core::telemetry::QueryRecord;
use commonplace_llm::{LlamaBackend, LoadOptions};
use std::io::Read;
use std::os::fd::{FromRawFd, OwnedFd};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

uniffi::setup_scaffolding!();

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum CpError {
    #[error("{msg}")]
    Failed { msg: String },
}

impl From<anyhow::Error> for CpError {
    fn from(e: anyhow::Error) -> Self {
        CpError::Failed { msg: format!("{e:#}") }
    }
}

type R<T> = Result<T, CpError>;

// ---------- records ----------

#[derive(uniffi::Record)]
pub struct EngineOptions {
    /// App files dir; the library lives in `<data_dir>/library`.
    pub data_dir: String,
    /// Directory with `leaf-mt/` and `ettin-17m/` encoder folders.
    pub encoders_dir: String,
    pub leaf_model: String,
    pub ettin_model: String,
    pub encoder_threads: u32,
    pub llm_threads: u32,
    pub pin_big_cores: bool,
}

#[derive(uniffi::Enum, Clone, Copy, PartialEq)]
pub enum ModelKind {
    Fast,
    Small,
    Deep,
}

impl From<ModelKind> for ModelRole {
    fn from(k: ModelKind) -> Self {
        match k {
            ModelKind::Fast => ModelRole::LlmFast,
            ModelKind::Small => ModelRole::LlmSmall,
            ModelKind::Deep => ModelRole::LlmDeep,
        }
    }
}

fn kind(r: &ModelRole) -> ModelKind {
    match r {
        ModelRole::LlmFast => ModelKind::Fast,
        ModelRole::LlmSmall => ModelKind::Small,
        ModelRole::LlmDeep => ModelKind::Deep,
    }
}

#[derive(uniffi::Record)]
pub struct PackInfo {
    pub pack_id: String,
    pub title: String,
    pub kind: String,
    pub snapshot_date: String,
    pub size_bytes: u64,
    pub articles: u64,
    pub passages: u64,
    pub license: String,
    pub attribution: String,
    pub has_dense: bool,
    pub has_cards: bool,
    /// False when the user switched the pack off: installed and counted, but not searched.
    pub enabled: bool,
    /// Built on the device from a document the user added ("My documents").
    pub user_document: bool,
}

#[derive(uniffi::Record)]
pub struct ModelPackInfo {
    pub pack_id: String,
    pub title: String,
    pub kind: ModelKind,
    pub size_bytes: u64,
    pub license: String,
    /// Absolute path of the model file (GGUF, or .litertlm for the LiteRT backend).
    pub file_path: String,
}

#[derive(uniffi::Record)]
pub struct SkippedPack {
    pub pack_id: String,
    pub reason: String,
}

#[derive(uniffi::Record)]
pub struct LibraryInfo {
    pub packs: Vec<PackInfo>,
    pub models: Vec<ModelPackInfo>,
    pub skipped: Vec<SkippedPack>,
    /// Installed, whether or not switched on (pack id `wikidata-facts`).
    pub has_wikidata: bool,
    pub wikidata_enabled: bool,
    pub wikidata_size_bytes: u64,
    pub total_bytes: u64,
    pub cap_bytes: u64,
    pub snapshot_date: String,
}

#[derive(uniffi::Record)]
pub struct ModelStatus {
    pub loaded: bool,
    pub kind: Option<ModelKind>,
    pub id: String,
}

#[derive(uniffi::Record, Clone)]
pub struct TurnRecord {
    pub query: String,
    pub answer: String,
    pub sources: Vec<SourceItem>,
}

/// What the engine did with a message: searched and answered, rewrote the last answer, or small talk.
#[derive(uniffi::Enum, Clone, Copy, PartialEq, Eq)]
pub enum TurnKind {
    Question,
    Reformat,
    Chat,
}

#[derive(uniffi::Record)]
pub struct AskInput {
    pub query: String,
    pub history: Vec<TurnRecord>,
    pub deep: bool,
    /// Thinking mode: the model reasons first (slower, capped by `Settings::think_budget`).
    pub think: bool,
    pub thermal_headroom: Option<f32>,
    /// Pack ids this question searches; empty means every enabled pack.
    pub packs: Vec<String>,
}

#[derive(uniffi::Enum, Clone, Copy)]
pub enum AskStage {
    Searching,
    Planning,
    Reading,
    Computing,
    Thinking,
    Writing,
    Done,
}

#[derive(uniffi::Record, Clone)]
pub struct SourceItem {
    pub n: u32,
    pub pack_id: String,
    pub pack_title: String,
    pub passage_id: u32,
    pub article_id: u32,
    pub title: String,
    pub section: String,
    pub snippet: String,
    /// License of the pack, and the article's web page (empty when it has none).
    pub license: String,
    pub source_url: String,
}

#[derive(uniffi::Record, Clone)]
pub struct FactItem {
    pub entity: String,
    pub label: String,
    pub value: String,
    pub when: Option<String>,
}

#[derive(uniffi::Record, Clone)]
pub struct AnswerCard {
    pub top: Option<SourceItem>,
    pub top_text: String,
    pub highlight: String,
    pub from_cards: bool,
    pub facts: Vec<FactItem>,
    pub computed: Vec<String>,
    pub sources: Vec<SourceItem>,
    pub entities: Vec<String>,
    pub card_ms: f64,
    /// Featured snippet from the extractive reader; arrives in a second `on_card`.
    pub featured: Option<FeaturedItem>,
}

#[derive(uniffi::Record, Clone)]
pub struct FeaturedItem {
    pub text: String,
    pub sentence: String,
    pub source: SourceItem,
}

#[derive(uniffi::Enum, Clone)]
pub enum AnswerSegment {
    Text { text: String },
    Unverified { text: String },
    Cite { n: u32 },
}

#[derive(uniffi::Record, Clone)]
pub struct Timing {
    pub card_ms: f64,
    pub ttft_ms: Option<f64>,
    pub total_ms: f64,
    pub prefill_tps: Option<f64>,
    pub decode_tps: Option<f64>,
    pub prompt_tokens: u32,
    pub cached_tokens: u32,
    pub gen_tokens: u32,
    pub evidence_tokens: u32,
    pub peak_rss_mb: f64,
    pub planner_used: bool,
    pub stages: Vec<StageTime>,
}

#[derive(uniffi::Record, Clone)]
pub struct StageTime {
    pub name: String,
    pub ms: f64,
}

#[derive(uniffi::Record)]
pub struct AskResult {
    pub kind: TurnKind,
    pub card: AnswerCard,
    pub sources: Vec<SourceItem>,
    pub segments: Vec<AnswerSegment>,
    pub answer_text: String,
    pub timing: Timing,
}

#[derive(uniffi::Record)]
pub struct QuerySummary {
    pub ts_ms: u64,
    pub query: String,
    pub model: String,
    pub deep: bool,
    pub timing: Timing,
    pub thermal_headroom: Option<f32>,
    pub threads: Option<u32>,
    pub error: Option<String>,
}

#[derive(uniffi::Record)]
pub struct PassageView {
    pub pack_id: String,
    pub pack_title: String,
    pub passage_id: u32,
    pub article_id: u32,
    pub title: String,
    pub section: String,
    pub text: String,
    pub url_title: String,
    pub license: String,
    pub attribution: String,
    /// The article's web page; empty when the pack has none.
    pub source_url: String,
}

#[derive(uniffi::Record)]
pub struct ArticleParagraph {
    pub passage_id: u32,
    pub section: String,
    pub text: String,
}

#[derive(uniffi::Record)]
pub struct ArticleView {
    pub pack_id: String,
    pub pack_title: String,
    pub article_id: u32,
    pub title: String,
    pub oneliner: String,
    pub paragraphs: Vec<ArticleParagraph>,
    pub attribution: String,
    pub license: String,
    pub source_url: String,
}

#[derive(uniffi::Record)]
pub struct ImportPart {
    /// Display name, e.g. `enwiki-core.tar`, `enwiki-core.tar.part001` or `enwiki-core.pack.json`.
    pub name: String,
    /// Detached file descriptor; Rust takes ownership and closes it.
    pub fd: i32,
    pub size: u64,
    /// Expected SHA-256 of the file (hex), from the catalog. When it is `None`, a `.pack.json` index supplies it.
    pub sha256: Option<String>,
}

#[derive(uniffi::Record)]
pub struct Settings {
    pub llm_threads: u32,
    pub evidence_tokens: u32,
    pub max_answer_tokens: u32,
    pub think_budget: u32,
    /// Rewrite each message into a standalone, typo-free search query first (one short model call).
    pub rewrite: bool,
    pub use_planner: bool,
    pub use_rerank: bool,
    pub use_dense: bool,
    pub nprobe: u32,
    pub rerank_keep: u32,
}

#[derive(uniffi::Record)]
pub struct DeviceInfo {
    pub llama_system_info: String,
    pub big_cores: Vec<u32>,
    pub build: String,
    pub cpu_features_ok: bool,
    pub cpu_features_error: String,
}

#[derive(uniffi::Record)]
pub struct GenStatsRecord {
    pub prompt_tokens: u32,
    pub gen_tokens: u32,
    pub prefill_ms: f64,
    pub decode_ms: f64,
}

// ---------- callbacks ----------

#[uniffi::export(with_foreign)]
pub trait AskListener: Send + Sync {
    fn on_stage(&self, stage: AskStage, detail: String);
    fn on_card(&self, card: AnswerCard);
    fn on_sources(&self, sources: Vec<SourceItem>);
    /// The reasoning so far in thinking mode; `done` when the answer starts.
    fn on_thinking(&self, text: String, done: bool);
    fn on_answer(&self, segments: Vec<AnswerSegment>, done: bool);
}

#[uniffi::export(with_foreign)]
pub trait ImportListener: Send + Sync {
    fn on_progress(&self, bytes_done: u64, bytes_total: u64);
    /// A part was fully read and verified; the app may offer to delete the source file.
    fn on_part_done(&self, name: String);
}

/// LiteRT-LM (or any Kotlin-side engine). Called on a Rust worker thread; must block until done.
#[uniffi::export(with_foreign)]
pub trait ForeignLlm: Send + Sync {
    fn id(&self) -> String;
    fn generate(&self, system: String, user: String, max_tokens: u32, temperature: f32, top_p: f32, json_only: bool, sink: Arc<TokenSink>) -> R<GenStatsRecord>;
}

/// Receives streamed tokens from a ForeignLlm. `on_token` returns false when the app wants it to stop.
#[derive(uniffi::Object)]
pub struct TokenSink {
    tx: Mutex<std::sync::mpsc::Sender<String>>,
    stop: AtomicBool,
}

#[uniffi::export]
impl TokenSink {
    pub fn on_token(&self, text: String) -> bool {
        let _ = self.tx.lock().unwrap().send(text);
        !self.stop.load(Ordering::Relaxed)
    }
}

struct ForeignAdapter(Arc<dyn ForeignLlm>);

impl LlmBackend for ForeignAdapter {
    fn id(&self) -> String {
        self.0.id()
    }

    fn generate(&self, system: &str, user: &str, p: &GenParams, on_token: &mut dyn FnMut(&str) -> bool) -> anyhow::Result<GenStats> {
        let (tx, rx) = std::sync::mpsc::channel();
        let sink = Arc::new(TokenSink { tx: Mutex::new(tx), stop: AtomicBool::new(false) });
        let (sys, usr, llm, s2) = (system.to_string(), user.to_string(), self.0.clone(), sink.clone());
        let (max, temp, top_p, json) = (p.max_tokens, p.temperature, p.top_p, p.grammar.is_some());
        let worker = std::thread::spawn(move || {
            let r = llm.generate(sys, usr, max, temp, top_p, json, s2.clone());
            // Close the channel so the receiver loop ends.
            *s2.tx.lock().unwrap() = std::sync::mpsc::channel().0;
            r
        });
        for tok in rx {
            if !on_token(&tok) {
                sink.stop.store(true, Ordering::Relaxed);
            }
        }
        let st = worker.join().map_err(|_| anyhow::anyhow!("foreign llm panicked"))?.map_err(|e| anyhow::anyhow!("{e}"))?;
        Ok(GenStats { prompt_tokens: st.prompt_tokens, gen_tokens: st.gen_tokens, prefill_ms: st.prefill_ms, decode_ms: st.decode_ms, ..Default::default() })
    }
}

// ---------- conversions ----------

fn source_item(s: &engine::SourceRef) -> SourceItem {
    SourceItem {
        n: s.n,
        pack_id: s.pack_id.clone(),
        pack_title: s.pack_title.clone(),
        passage_id: s.passage_id,
        article_id: s.article_id,
        title: s.title.clone(),
        section: s.section.clone(),
        snippet: s.snippet.clone(),
        license: s.license.clone(),
        source_url: s.source_url.clone(),
    }
}

fn source_ref(s: &SourceItem) -> engine::SourceRef {
    engine::SourceRef {
        n: s.n,
        pack_id: s.pack_id.clone(),
        pack_title: s.pack_title.clone(),
        passage_id: s.passage_id,
        article_id: s.article_id,
        title: s.title.clone(),
        section: s.section.clone(),
        snippet: s.snippet.clone(),
        score: 0.0,
        license: s.license.clone(),
        source_url: s.source_url.clone(),
    }
}

fn card(c: &engine::Card) -> AnswerCard {
    AnswerCard {
        top: c.top.as_ref().map(|t| source_item(&t.source)),
        top_text: c.top.as_ref().map(|t| t.text.clone()).unwrap_or_default(),
        highlight: c.top.as_ref().map(|t| t.highlight.clone()).unwrap_or_default(),
        from_cards: c.top.as_ref().is_some_and(|t| t.from_cards),
        facts: c.facts.iter().map(|f| FactItem { entity: f.entity.clone(), label: f.label.clone(), value: f.value.clone(), when: f.point_in_time.clone() }).collect(),
        computed: c.computed.clone(),
        sources: c.sources.iter().map(source_item).collect(),
        entities: c.entities.clone(),
        card_ms: c.card_ms,
        featured: c.featured.as_ref().map(|f| FeaturedItem { text: f.text.clone(), sentence: f.sentence.clone(), source: source_item(&f.source) }),
    }
}

fn segments(s: &[Segment]) -> Vec<AnswerSegment> {
    s.iter()
        .map(|x| match x {
            Segment::Text(t) => AnswerSegment::Text { text: t.clone() },
            Segment::Unverified(t) => AnswerSegment::Unverified { text: t.clone() },
            Segment::Cite(n) => AnswerSegment::Cite { n: *n },
        })
        .collect()
}

fn timing(r: &QueryRecord) -> Timing {
    let s = r.synthesis.as_ref();
    Timing {
        card_ms: r.card_ms,
        ttft_ms: r.ttft_ms,
        total_ms: r.total_ms,
        prefill_tps: s.map(|s| s.prefill_tps()),
        decode_tps: s.map(|s| s.decode_tps()),
        prompt_tokens: s.map(|s| s.prompt_tokens).unwrap_or(0),
        cached_tokens: s.map(|s| s.cached_tokens).unwrap_or(0),
        gen_tokens: s.map(|s| s.gen_tokens).unwrap_or(0),
        evidence_tokens: r.evidence_tokens as u32,
        peak_rss_mb: r.peak_rss_mb,
        planner_used: r.planner_used,
        stages: r.stages.iter().map(|(n, ms)| StageTime { name: n.clone(), ms: *ms }).collect(),
    }
}

struct ListenerSink {
    l: Arc<dyn AskListener>,
    cancel: Arc<AtomicBool>,
}

impl EventSink for ListenerSink {
    fn event(&self, e: Event) {
        match e {
            Event::Stage { stage, detail } => self.l.on_stage(
                match stage {
                    Stage::Searching => AskStage::Searching,
                    Stage::Planning => AskStage::Planning,
                    Stage::Reading => AskStage::Reading,
                    Stage::Computing => AskStage::Computing,
                    Stage::Thinking => AskStage::Thinking,
                    Stage::Writing => AskStage::Writing,
                    Stage::Done => AskStage::Done,
                },
                detail,
            ),
            Event::Card(c) => self.l.on_card(card(&c)),
            Event::Sources(s) => self.l.on_sources(s.iter().map(source_item).collect()),
            Event::Thinking { text, done } => self.l.on_thinking(text, done),
            Event::Answer { segments: s, done, .. } => self.l.on_answer(segments(&s), done),
        }
    }

    fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::Relaxed)
    }
}

struct ImportProgressAdapter {
    l: Arc<dyn ImportListener>,
    names: Vec<String>,
    total: u64,
    last: u64,
}

impl import::ImportProgress for ImportProgressAdapter {
    fn bytes(&mut self, done: u64) {
        if done - self.last >= 8 << 20 || done == self.total {
            self.last = done;
            self.l.on_progress(done, self.total);
        }
    }
    fn part_done(&mut self, index: usize) {
        self.l.on_part_done(self.names[index].clone());
    }
}

// ---------- engine ----------

#[derive(uniffi::Object)]
pub struct CommonplaceEngine {
    engine: Engine,
    cancel: Arc<AtomicBool>,
    data_dir: PathBuf,
}

fn logger_init() {
    #[cfg(target_os = "android")]
    android_logger::init_once(android_logger::Config::default().with_max_level(log::LevelFilter::Info).with_tag("commonplace"));
}

#[uniffi::export]
impl CommonplaceEngine {
    #[uniffi::constructor]
    pub fn new(opts: EngineOptions) -> R<Arc<Self>> {
        logger_init();
        let data_dir = PathBuf::from(&opts.data_dir);
        let enc = PathBuf::from(&opts.encoders_dir);
        let leaf = enc.join("leaf-mt");
        let ettin = enc.join("ettin-17m");
        let reader = enc.join("reader");
        let cfg = EngineConfig {
            library_dir: data_dir.join("library"),
            leaf_dir: leaf.join(&opts.leaf_model).exists().then_some(leaf),
            leaf_model: opts.leaf_model.clone(),
            ettin_dir: ettin.join(&opts.ettin_model).exists().then_some(ettin),
            ettin_model: opts.ettin_model.clone(),
            reader_dir: reader.join("onnx/model_int8.onnx").exists().then_some(reader),
            reader_model: "onnx/model_int8.onnx".into(),
            ort_dylib: None,
            encoder_threads: opts.encoder_threads as usize,
            telemetry_path: Some(data_dir.join("telemetry.jsonl")),
        };
        let cpus: Vec<usize> = if opts.pin_big_cores { commonplace_llm::big_cores() } else { vec![] };
        let loader: LlmLoader = Box::new(move |mp, threads| {
            let file = mp.file().ok_or_else(|| anyhow::anyhow!("model pack has no file"))?;
            let n_ctx = mp.manifest.model.as_ref().map(|m| m.n_ctx).unwrap_or(4096);
            let deep = mp.role() == Some(&ModelRole::LlmDeep);
            let batch_threads = (cpus.len() as u32).max(threads);
            let llm = LlamaBackend::load(&file, &LoadOptions { n_ctx, threads, batch_threads, cpus: cpus.clone(), in_ram: !deep })?;
            Ok(Arc::new(llm) as Arc<dyn LlmBackend>)
        });
        let engine = Engine::new(cfg, Some(loader))?;
        {
            let mut s = engine.settings.write().unwrap();
            s.llm_threads = opts.llm_threads;
            // Phone rerank budget: the 8-bit weight-only reranker on the top 24 fused passages costs about what the
            // int8 one did on 40 and ranks better (NQ hit@1 0.510 vs 0.485; docs/BENCHMARKS.md).
            s.retrieval.fuse_keep = 24;
        }
        Ok(Arc::new(Self { engine, cancel: Arc::new(AtomicBool::new(false)), data_dir }))
    }

    pub fn library(&self) -> LibraryInfo {
        let lib: Arc<Library> = self.engine.library();
        let info = |m: &pack::Manifest, has_dense: bool, has_cards: bool, enabled: bool| PackInfo {
            pack_id: m.pack_id.clone(),
            title: m.title.clone(),
            kind: "knowledge".into(),
            snapshot_date: m.snapshot_date.clone(),
            size_bytes: m.size_bytes,
            articles: m.counts.as_ref().map(|c| c.articles).unwrap_or(0),
            passages: m.counts.as_ref().map(|c| c.passages).unwrap_or(0),
            license: m.license.clone(),
            attribution: m.attribution.clone(),
            has_dense,
            has_cards,
            enabled,
            user_document: m.user_document,
        };
        let packs_dir = Library::packs_dir(&lib.root);
        let packs = lib
            .packs
            .iter()
            .map(|p| info(&p.manifest, p.dense.is_some(), p.cards.is_some(), true))
            .chain(lib.disabled.iter().filter(|m| m.pack_type == PackType::Knowledge).map(|m| {
                let dir = packs_dir.join(&m.pack_id);
                info(m, dir.join("dense").exists(), dir.join("cards").exists(), false)
            }))
            .collect();
        let wikidata = lib.wikidata.as_ref().map(|(m, _)| m).or_else(|| lib.disabled.iter().find(|m| m.pack_type == PackType::Wikidata));
        let models = lib
            .models
            .iter()
            .filter_map(|m| {
                Some(ModelPackInfo {
                    pack_id: m.manifest.pack_id.clone(),
                    title: m.manifest.title.clone(),
                    kind: kind(m.role()?),
                    size_bytes: m.manifest.size_bytes,
                    license: m.manifest.license.clone(),
                    file_path: m.file().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default(),
                })
            })
            .collect();
        LibraryInfo {
            packs,
            models,
            skipped: lib.skipped.iter().map(|(p, r)| SkippedPack { pack_id: p.clone(), reason: r.clone() }).collect(),
            has_wikidata: wikidata.is_some(),
            wikidata_enabled: lib.wikidata.is_some(),
            wikidata_size_bytes: wikidata.map(|m| m.size_bytes).unwrap_or(0),
            total_bytes: lib.total_bytes(),
            cap_bytes: FOOTPRINT_CAP,
            snapshot_date: lib.snapshot_date(),
        }
    }

    /// Load the best installed model: fast, else small. Returns the status either way.
    pub fn load_default_model(&self) -> R<ModelStatus> {
        let lib = self.engine.library();
        for k in [ModelKind::Fast, ModelKind::Small] {
            if lib.model(&k.into()).is_some() {
                self.engine.load_model(k.into())?;
                break;
            }
        }
        Ok(self.model_status())
    }

    pub fn load_model(&self, kind: ModelKind) -> R<ModelStatus> {
        self.engine.load_model(kind.into())?;
        Ok(self.model_status())
    }

    pub fn unload_model(&self) {
        self.engine.unload_model();
    }

    pub fn model_status(&self) -> ModelStatus {
        match self.engine.llm() {
            Some((role, l)) => ModelStatus { loaded: true, kind: Some(kind(&role)), id: l.id() },
            None => ModelStatus { loaded: false, kind: None, id: String::new() },
        }
    }

    /// Use a Kotlin-side engine (LiteRT-LM) instead of llama.cpp. `None` unloads it.
    pub fn set_foreign_llm(&self, llm: Option<Arc<dyn ForeignLlm>>) {
        self.engine.set_llm(ModelRole::LlmFast, llm.map(|l| Arc::new(ForeignAdapter(l)) as Arc<dyn LlmBackend>));
    }

    pub fn ask(&self, input: AskInput, listener: Arc<dyn AskListener>) -> R<AskResult> {
        self.cancel.store(false, Ordering::Relaxed);
        self.engine.set_thermal_headroom(input.thermal_headroom);
        let req = AskRequest {
            query: input.query,
            history: input
                .history
                .into_iter()
                .map(|t| engine::Turn { query: t.query, answer: t.answer, sources: t.sources.iter().map(source_ref).collect() })
                .collect(),
            deep: input.deep,
            think: input.think,
            packs: input.packs,
        };
        let sink = ListenerSink { l: listener, cancel: self.cancel.clone() };
        let out = self.engine.ask(&req, &sink)?;
        Ok(AskResult {
            kind: match out.kind {
                engine::TurnKind::Question => TurnKind::Question,
                engine::TurnKind::Reformat => TurnKind::Reformat,
                engine::TurnKind::Chat => TurnKind::Chat,
            },
            card: card(&out.card),
            sources: out.sources.iter().map(source_item).collect(),
            answer_text: commonplace_core::citations::plain_text(&out.segments),
            segments: segments(&out.segments),
            timing: timing(&out.record),
        })
    }

    pub fn cancel(&self) {
        self.cancel.store(true, Ordering::Relaxed);
    }

    pub fn passage(&self, pack_id: String, passage_id: u32) -> R<PassageView> {
        let lib = self.engine.library();
        let p = lib.packs.iter().find(|p| p.manifest.pack_id == pack_id).ok_or_else(|| CpError::Failed { msg: format!("pack {pack_id} not installed") })?;
        let rec = p.passage(passage_id)?;
        let a = p.meta.article(rec.article_id)?.ok_or_else(|| CpError::Failed { msg: "article not found".into() })?;
        let source_url = pack::source_url(&pack_id, &a.title, a.url_title.as_deref().unwrap_or_default()).unwrap_or_default();
        Ok(PassageView {
            pack_id,
            pack_title: p.manifest.title.clone(),
            passage_id,
            article_id: a.id,
            title: a.title,
            section: rec.section_path,
            text: rec.text,
            source_url,
            url_title: a.url_title.unwrap_or_default(),
            license: p.manifest.license.clone(),
            attribution: p.manifest.attribution.clone(),
        })
    }

    pub fn article(&self, pack_id: String, article_id: u32) -> R<ArticleView> {
        let lib = self.engine.library();
        let p = lib.packs.iter().find(|p| p.manifest.pack_id == pack_id).ok_or_else(|| CpError::Failed { msg: format!("pack {pack_id} not installed") })?;
        let a = p.meta.article(article_id)?.ok_or_else(|| CpError::Failed { msg: "article not found".into() })?;
        let ids: Vec<u32> = (a.first_passage..a.first_passage + a.n_passages).collect();
        let recs = p.passages(&ids)?;
        let source_url = pack::source_url(&pack_id, &a.title, a.url_title.as_deref().unwrap_or_default()).unwrap_or_default();
        Ok(ArticleView {
            pack_id,
            pack_title: p.manifest.title.clone(),
            article_id,
            title: a.title,
            oneliner: a.oneliner.unwrap_or_default(),
            paragraphs: ids.into_iter().zip(recs).map(|(id, r)| ArticleParagraph { passage_id: id, section: r.section_path, text: r.text }).collect(),
            attribution: p.manifest.attribution.clone(),
            license: p.manifest.license.clone(),
            source_url,
        })
    }

    /// The pack's `NOTICE.txt` (credit, license and license texts); empty when it has none.
    pub fn pack_notice(&self, pack_id: String) -> String {
        if pack_id.contains('/') || pack_id.starts_with('.') {
            return String::new();
        }
        std::fs::read_to_string(Library::packs_dir(&self.engine.cfg.library_dir).join(pack_id).join("NOTICE.txt")).unwrap_or_default()
    }

    /// Stream-import a pack from its parts (any order; sorted by name). Returns the pack id.
    pub fn import_pack(&self, parts: Vec<ImportPart>, listener: Arc<dyn ImportListener>) -> R<String> {
        let mut index: Option<import::PartIndex> = None;
        let mut tars: Vec<(String, OwnedFd, u64, Option<String>)> = Vec::new();
        for p in parts {
            // SAFETY: Kotlin detached this fd and hands us ownership.
            let fd = unsafe { OwnedFd::from_raw_fd(p.fd) };
            if p.name.ends_with(".pack.json") {
                let mut s = String::new();
                std::fs::File::from(fd).read_to_string(&mut s).map_err(anyhow::Error::from)?;
                index = Some(serde_json::from_str(&s).map_err(anyhow::Error::from)?);
            } else {
                tars.push((p.name, fd, p.size, p.sha256));
            }
        }
        if tars.is_empty() {
            return Err(CpError::Failed { msg: "no pack parts selected".into() });
        }
        tars.sort_by(|a, b| a.0.cmp(&b.0));
        let total: u64 = tars.iter().map(|t| t.2).sum();
        let lib = self.engine.library();
        if lib.total_bytes() + total > FOOTPRINT_CAP {
            return Err(CpError::Failed {
                msg: format!("importing {:.1} GB would exceed the 50 GB limit ({:.1} GB used)", total as f64 / 1e9, lib.total_bytes() as f64 / 1e9),
            });
        }
        if let Some(ix) = &index {
            if ix.parts.len() != tars.len() {
                return Err(CpError::Failed { msg: format!("{} needs {} parts, {} selected", ix.pack_id, ix.parts.len(), tars.len()) });
            }
        }
        let expected: Vec<Option<String>> = tars
            .iter()
            .map(|(name, _, _, sha)| {
                sha.clone().or_else(|| index.as_ref().and_then(|ix| ix.parts.iter().find(|p| &p.name == name).map(|p| p.sha256.clone())))
            })
            .collect();
        let names: Vec<String> = tars.iter().map(|t| t.0.clone()).collect();
        let readers: Vec<Box<dyn Read + Send>> =
            tars.into_iter().map(|(_, fd, _, _)| Box::new(std::io::BufReader::with_capacity(1 << 20, std::fs::File::from(fd))) as Box<dyn Read + Send>).collect();
        let mut prog = ImportProgressAdapter { l: listener, names, total, last: 0 };
        let packs_dir = Library::packs_dir(&self.engine.cfg.library_dir);
        let m = import::import(readers, expected, &packs_dir, &mut prog, &|m| lib.check_compatible(m))?;
        self.engine.reload_library()?;
        Ok(m.pack_id)
    }

    /// Index a document the user added (text per page, page 1 first) as the pack `doc-<hash>`. Returns the pack id.
    /// `on_progress(done, total)` counts embedded passages; `on_part_done` is not called. Fails with a message the
    /// UI can show for an empty document, one over 5,000 passages, or one that is already added.
    pub fn add_document(&self, title: String, pages: Vec<String>, listener: Arc<dyn ImportListener>) -> R<String> {
        Ok(self.engine.add_document(&title, &pages, &mut |done, total| listener.on_progress(done as u64, total as u64))?)
    }

    pub fn remove_pack(&self, pack_id: String) -> R<()> {
        let dir = Library::packs_dir(&self.engine.cfg.library_dir).join(&pack_id);
        if !dir.starts_with(Library::packs_dir(&self.engine.cfg.library_dir)) || pack_id.contains('/') || pack_id.starts_with('.') {
            return Err(CpError::Failed { msg: "invalid pack id".into() });
        }
        let m = pack::Manifest::read(&dir)?;
        if m.pack_type == PackType::Model
            && self.engine.llm().is_some_and(|(r, _)| m.model.as_ref().is_some_and(|mi| mi.role == r))
        {
            self.engine.unload_model();
        }
        // Drop open readers before deleting their files.
        let tmp = dir.with_file_name(format!(".removing-{pack_id}"));
        std::fs::rename(&dir, &tmp).map_err(anyhow::Error::from)?;
        Library::set_enabled(&self.engine.cfg.library_dir, &pack_id, true)?;
        self.engine.reload_library()?;
        std::fs::remove_dir_all(&tmp).map_err(anyhow::Error::from)?;
        Ok(())
    }

    /// Switch a knowledge or Wikidata pack on or off. Persists in the library and keeps the loaded model.
    pub fn set_pack_enabled(&self, pack_id: String, enabled: bool) -> R<()> {
        Ok(self.engine.set_pack_enabled(&pack_id, enabled)?)
    }

    /// Re-hash a pack. Returns the files that do not match its manifest.
    pub fn verify_pack(&self, pack_id: String) -> R<Vec<String>> {
        Ok(import::verify(&Library::packs_dir(&self.engine.cfg.library_dir).join(pack_id))?)
    }

    pub fn reload(&self) -> R<()> {
        Ok(self.engine.reload_library()?)
    }

    pub fn recent_queries(&self) -> Vec<QuerySummary> {
        self.engine
            .recent()
            .iter()
            .rev()
            .map(|r| QuerySummary {
                ts_ms: r.ts_ms,
                query: r.query.clone(),
                model: r.model_id.clone().unwrap_or_default(),
                deep: r.deep,
                timing: timing(r),
                thermal_headroom: r.thermal_headroom,
                threads: r.threads,
                error: r.error.clone(),
            })
            .collect()
    }

    pub fn telemetry_path(&self) -> String {
        self.data_dir.join("telemetry.jsonl").to_string_lossy().into_owned()
    }

    pub fn settings(&self) -> Settings {
        let s = self.engine.settings.read().unwrap();
        Settings {
            llm_threads: s.llm_threads,
            evidence_tokens: s.evidence_tokens as u32,
            max_answer_tokens: s.max_answer_tokens,
            think_budget: s.think_budget,
            rewrite: s.rewrite,
            use_planner: s.use_planner,
            use_rerank: s.retrieval.use_rerank,
            use_dense: s.retrieval.use_dense,
            nprobe: s.retrieval.nprobe as u32,
            rerank_keep: s.retrieval.rerank_keep as u32,
        }
    }

    pub fn update_settings(&self, n: Settings) {
        let mut s = self.engine.settings.write().unwrap();
        s.llm_threads = n.llm_threads.clamp(1, 8);
        s.evidence_tokens = n.evidence_tokens.clamp(200, 4000) as usize;
        s.max_answer_tokens = n.max_answer_tokens.clamp(64, 1024);
        s.think_budget = n.think_budget.clamp(64, 2048);
        s.rewrite = n.rewrite;
        s.use_planner = n.use_planner;
        s.retrieval.use_rerank = n.use_rerank;
        s.retrieval.use_dense = n.use_dense;
        s.retrieval.nprobe = n.nprobe.clamp(1, 512) as usize;
        s.retrieval.rerank_keep = n.rerank_keep.clamp(2, 16) as usize;
        if let Some((_, l)) = self.engine.llm() {
            l.set_threads(s.llm_threads);
        }
    }

    pub fn device_info(&self) -> DeviceInfo {
        let check = commonplace_llm::check_cpu_features();
        DeviceInfo {
            llama_system_info: commonplace_llm::system_info(),
            big_cores: commonplace_llm::big_cores().into_iter().map(|c| c as u32).collect(),
            build: commonplace_core::telemetry::BUILD.to_string(),
            cpu_features_ok: check.is_ok(),
            cpu_features_error: check.err().map(|e| e.to_string()).unwrap_or_default(),
        }
    }
}
