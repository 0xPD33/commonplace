//! The orchestrator: Plan → Retrieve → Compute → Synthesize (PLAN.md §9), with streaming events.

use crate::citations::{self, Segment};
use crate::encoders::{Head, QueryEncoder, Reader, Reranker};
use crate::evidence::{self, Evidence};
use crate::library::{Library, ModelPack};
use crate::llm::{GenParams, GenStats, LlmBackend};
use crate::pack::ModelRole;
use crate::prompts;
use crate::retrieval::{Hit, RetrievalSettings, Retriever, select_diverse};
use crate::route::{self, Route};
use crate::telemetry::{self, QueryRecord};
use crate::tools::{calc, units, wikidata::WdFact, wikidata::format_num};
use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::{HashSet, VecDeque};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};
use std::time::Instant;

#[derive(Debug, Clone)]
pub struct EngineConfig {
    pub library_dir: PathBuf,
    /// Directory with leaf-mt `tokenizer.json` and ONNX file `leaf_model` (relative).
    pub leaf_dir: Option<PathBuf>,
    pub leaf_model: String,
    pub ettin_dir: Option<PathBuf>,
    pub ettin_model: String,
    /// Directory with the extractive reader `tokenizer.json` and ONNX file `reader_model` (relative).
    pub reader_dir: Option<PathBuf>,
    pub reader_model: String,
    pub ort_dylib: Option<PathBuf>,
    pub encoder_threads: usize,
    pub telemetry_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Settings {
    pub retrieval: RetrievalSettings,
    pub evidence_tokens: usize,
    pub deep_evidence_tokens: usize,
    /// Leave out passages whose rerank score is more than this below the best one (keeps at least 3).
    pub evidence_score_gap: Option<f32>,
    pub max_answer_tokens: u32,
    pub deep_max_answer_tokens: u32,
    /// Let the model rewrite each message into a standalone, typo-free search query first.
    pub rewrite: bool,
    pub use_planner: bool,
    pub use_compute: bool,
    pub use_cards: bool,
    pub llm_threads: u32,
    /// Reasoning-token cap in thinking mode (~14 tok/s on a warm Pixel: 256 tokens ≈ 18 s).
    pub think_budget: u32,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            retrieval: RetrievalSettings::default(),
            evidence_tokens: 600,
            deep_evidence_tokens: 2500,
            evidence_score_gap: None,
            max_answer_tokens: 400,
            deep_max_answer_tokens: 700,
            use_planner: true,
            // Off: on the Pixel it doubles the card time (2.0 vs 1.0 s) and did not raise the seeds score (docs/BENCHMARKS.md).
            rewrite: false,
            use_compute: true,
            use_cards: true,
            llm_threads: 4,
            think_budget: 256,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Turn {
    pub query: String,
    pub answer: String,
    /// The answer's numbered sources; a reformat keeps them, so its citations stay valid.
    #[serde(default)]
    pub sources: Vec<SourceRef>,
}

/// What a message asks for: a question to search and answer, a rewrite of the last answer ("make it
/// shorter"), or small talk. The turn-kind head decides (`encoders::Head::turn`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize)]
pub enum TurnKind {
    #[default]
    Question,
    Reformat,
    Chat,
}

/// The rewrite model's reading of a question: a standalone search query, its topics, and whether it
/// compares things.
struct Rewrite {
    compare: bool,
    query: String,
    topics: Vec<String>,
}

#[derive(Debug, Clone, Default)]
pub struct AskRequest {
    pub query: String,
    pub history: Vec<Turn>,
    pub deep: bool,
    /// Let the model reason before it answers (slower; capped by `Settings::think_budget`).
    pub think: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Stage {
    Searching,
    Planning,
    Reading,
    Computing,
    Thinking,
    Writing,
    Done,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceRef {
    /// Citation number, or 0 on the instant card before evidence is numbered.
    pub n: u32,
    pub pack_id: String,
    pub pack_title: String,
    pub passage_id: u32,
    pub article_id: u32,
    pub title: String,
    pub section: String,
    pub snippet: String,
    pub score: f32,
}

#[derive(Debug, Clone, Serialize)]
pub struct CardPassage {
    pub source: SourceRef,
    pub text: String,
    pub highlight: String,
    pub from_cards: bool,
}

/// Reader margin (answer vs "no answer" logits) a span needs to be shown. It filters weak spans but not
/// confident wrong ones (seen: 17.4 for a wrong answer), so the UI always names the source.
const FEATURED_MIN_MARGIN: f32 = 3.0;

/// Featured snippet: a short answer span, the sentence around it, and where it came from.
#[derive(Debug, Clone, Serialize)]
pub struct Featured {
    pub text: String,
    pub sentence: String,
    pub source: SourceRef,
    pub margin: f32,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct Card {
    pub query: String,
    pub top: Option<CardPassage>,
    pub facts: Vec<WdFact>,
    pub computed: Vec<String>,
    pub sources: Vec<SourceRef>,
    pub entities: Vec<String>,
    pub card_ms: f64,
    /// Featured snippet: the reader's answer span, set in a second `Card` event.
    pub featured: Option<Featured>,
}

#[derive(Debug, Clone)]
pub enum Event {
    Stage { stage: Stage, detail: String },
    Card(Card),
    Sources(Vec<SourceRef>),
    /// The model's reasoning so far (thinking mode); `done` when the answer starts.
    Thinking { text: String, done: bool },
    /// `raw` is the model text so far; `segments` is the checked rendering of it.
    Answer { segments: Vec<Segment>, raw: String, done: bool },
}

pub trait EventSink: Send + Sync {
    fn event(&self, e: Event);
    fn cancelled(&self) -> bool {
        false
    }
}

#[derive(Debug, Clone)]
pub struct AskOutcome {
    pub kind: TurnKind,
    pub card: Card,
    pub sources: Vec<SourceRef>,
    pub segments: Vec<Segment>,
    pub record: QueryRecord,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct Plan {
    #[serde(default)]
    standalone_query: String,
    #[serde(default)]
    intent: String,
    #[serde(default)]
    subqueries: Vec<String>,
    #[serde(default)]
    needs_numbers: bool,
}

#[derive(Debug, Clone, Deserialize)]
struct Calc {
    label: String,
    expr: String,
}

#[derive(Debug, Clone, Deserialize)]
struct Calcs {
    #[serde(default)]
    calcs: Vec<Calc>,
}

/// Parse the first JSON object in `s` (tolerant of prose around it).
fn parse_json<T: for<'de> Deserialize<'de>>(s: &str) -> Option<T> {
    let (a, b) = (s.find('{')?, s.rfind('}')?);
    serde_json::from_str(&s[a..=b]).ok()
}

pub type LlmLoader = Box<dyn Fn(&ModelPack, u32) -> Result<Arc<dyn LlmBackend>> + Send + Sync>;

pub struct Engine {
    pub cfg: EngineConfig,
    lib: RwLock<Arc<Library>>,
    enc: Option<QueryEncoder>,
    rr: Option<Reranker>,
    reader: Option<Reader>,
    llm: RwLock<Option<(ModelRole, Arc<dyn LlmBackend>)>>,
    loader: Option<LlmLoader>,
    pub settings: RwLock<Settings>,
    recent: Mutex<VecDeque<QueryRecord>>,
    thermal: Mutex<Option<f32>>,
}

const RECENT: usize = 20;

impl Engine {
    pub fn new(cfg: EngineConfig, loader: Option<LlmLoader>) -> Result<Self> {
        let lib = Library::open(&cfg.library_dir)?;
        let needs_ort = cfg.leaf_dir.is_some() || cfg.ettin_dir.is_some() || cfg.reader_dir.is_some();
        if needs_ort {
            crate::encoders::init_ort(cfg.ort_dylib.as_deref())?;
        }
        let dims = lib.embedder.as_ref().map(|e| e.dims as usize).unwrap_or(512);
        let enc = match &cfg.leaf_dir {
            Some(d) => Some(QueryEncoder::load(d, &cfg.leaf_model, cfg.encoder_threads, dims)?),
            None => None,
        };
        let rr = match &cfg.ettin_dir {
            Some(d) => Some(Reranker::load(d, &cfg.ettin_model, cfg.encoder_threads)?),
            None => None,
        };
        let reader = match &cfg.reader_dir {
            Some(d) => Some(Reader::load(d, &cfg.reader_model, cfg.encoder_threads)?),
            None => None,
        };
        Ok(Self {
            cfg,
            lib: RwLock::new(Arc::new(lib)),
            enc,
            rr,
            reader,
            llm: RwLock::new(None),
            loader,
            settings: RwLock::new(Settings::default()),
            recent: Mutex::new(VecDeque::new()),
            thermal: Mutex::new(None),
        })
    }

    pub fn library(&self) -> Arc<Library> {
        self.lib.read().unwrap().clone()
    }

    /// The fused pool the reranker would see for `query` (no rewrite, no follow-up context), in fusion
    /// order with the reranker's scores. Builds reranker training data (`commonplace pool`).
    pub fn pool(&self, query: &str) -> Result<(Arc<Library>, Vec<Hit>)> {
        let lib = self.library();
        let s = self.settings.read().unwrap().clone();
        let route = route::route(&lib, query, false, self.rr.as_ref(), &[]);
        let retriever = Retriever { lib: &lib, enc: self.enc.as_ref(), rr: self.rr.as_ref(), s: &s.retrieval };
        let mut st = Default::default();
        let mut hits = retriever.fused(&route.query, &route.entities, &mut st)?;
        if let Some(rr) = self.rr.as_ref().filter(|_| s.retrieval.use_rerank) {
            let docs: Vec<String> = hits.iter().map(Hit::rerank_text).collect();
            for (h, s) in hits.iter_mut().zip(rr.score(query, &docs)?) {
                h.rerank = Some(s);
            }
        }
        Ok((lib, hits))
    }

    /// Re-scan the pack directory (after an import or removal). Keeps the loaded LLM.
    pub fn reload_library(&self) -> Result<()> {
        let lib = Library::open(&self.cfg.library_dir)?;
        *self.lib.write().unwrap() = Arc::new(lib);
        Ok(())
    }

    pub fn set_llm(&self, role: ModelRole, llm: Option<Arc<dyn LlmBackend>>) {
        *self.llm.write().unwrap() = llm.map(|l| (role, l));
    }

    pub fn llm(&self) -> Option<(ModelRole, Arc<dyn LlmBackend>)> {
        self.llm.read().unwrap().clone()
    }

    /// Load the model pack with `role` through the configured loader, unloading the current one first.
    pub fn load_model(&self, role: ModelRole) -> Result<()> {
        let loader = self.loader.as_ref().context("no model loader configured")?;
        let lib = self.library();
        let mp = lib.model(&role).with_context(|| format!("no {role:?} model installed"))?;
        *self.llm.write().unwrap() = None;
        let threads = self.settings.read().unwrap().llm_threads;
        let llm = loader(mp, threads)?;
        Self::warm_up(llm.as_ref(), &lib.snapshot_date());
        *self.llm.write().unwrap() = Some((role, llm));
        Ok(())
    }

    /// Prefill the fixed rewrite and answer prompts once, so the first question does not pay for them
    /// (the backend caches a system prompt's state after its first use). Seconds on a phone CPU.
    pub fn warm_up(llm: &dyn LlmBackend, snapshot_date: &str) {
        let one = GenParams::greedy(1);
        let _ = llm.generate(prompts::REWRITE_SYSTEM, "Message: hello", &one, &mut |_| false);
        let _ = llm.generate(&prompts::synthesis_system(snapshot_date), "Question: hello", &one, &mut |_| false);
    }

    pub fn unload_model(&self) {
        *self.llm.write().unwrap() = None;
    }

    pub fn set_thermal_headroom(&self, h: Option<f32>) {
        *self.thermal.lock().unwrap() = h;
    }

    pub fn recent(&self) -> Vec<QueryRecord> {
        self.recent.lock().unwrap().iter().cloned().collect()
    }

    fn source_ref(lib: &Library, h: &Hit, n: u32) -> SourceRef {
        let pack = &lib.packs[h.pack as usize].manifest;
        let snippet: String = h.passage.text.chars().take(160).collect();
        SourceRef {
            n,
            pack_id: pack.pack_id.clone(),
            pack_title: pack.title.clone(),
            passage_id: h.pid,
            article_id: h.article.id,
            title: h.article.title.clone(),
            section: h.passage.section_path.clone(),
            snippet,
            score: h.score(),
        }
    }

    fn wikidata_facts(lib: &Library, route: &Route, total: usize) -> Vec<WdFact> {
        let Some((_, wd)) = &lib.wikidata else { return vec![] };
        let qids: Vec<&str> = route.entities.iter().filter_map(|e| e.article.qid.as_deref()).collect();
        if qids.is_empty() {
            return vec![];
        }
        let per = (total / qids.len()).max(3);
        let mut qterms: std::collections::HashSet<String> = crate::text::terms(&route.query).into_iter().collect();
        // Everyday words for the most asked properties (terms are 6-character stems).
        for (words, prop) in [
            (&["tall", "high", "height"][..], "elevat"),
            (&["big", "large", "size", "square"], "area"),
            (&["people", "inhabi", "live", "reside"], "popula"),
            (&["born", "birth", "old", "age"], "birth"),
            (&["died", "death", "dead"], "death"),
            (&["founde", "built", "establ", "old"], "incept"),
            (&["long", "length"], "length"),
            (&["money", "curren"], "curren"),
            (&["speak", "langua", "spoken"], "langua"),
            (&["leader", "presid", "prime", "ruler", "head"], "head"),
            (&["study", "studie", "univer", "colleg", "school", "attend", "gradua", "alma"], "educat"),
            (&["wife", "husban", "marrie", "spouse"], "spouse"),
            (&["child", "childr", "son", "sons", "daught", "kids"], "child"),
            (&["work", "worked", "employ", "job"], "employ"),
            (&["award", "awards", "prize", "prizes", "won", "honour", "honor"], "award"),
        ] {
            if words.iter().any(|w| qterms.contains(*w)) {
                qterms.insert(prop.to_string());
            }
        }
        let all: Vec<Vec<WdFact>> = qids
            .iter()
            .map(|q| {
                wd.facts(q, 40)
                    .unwrap_or_default()
                    .into_iter()
                    // "Malta — country: Malta" and class labels tell the reader nothing.
                    .filter(|f| f.value != f.entity && !matches!(f.label.as_str(), "instance of" | "official name" | "country"))
                    .collect()
            })
            .collect();
        let mut out = Vec::new();
        for facts in &all {
            let mut facts = facts.clone();
            // Facts the question asks about first, then properties every entity has (so a comparison lines
            // up), then the pack's priority order (stable sort).
            let shared = |f: &WdFact| all.iter().all(|o| o.iter().any(|g| g.pid == f.pid));
            facts.sort_by_key(|f| (!crate::text::terms(&f.label).iter().any(|t| qterms.contains(t)), !shared(f)));
            out.extend(facts.into_iter().take(per));
        }
        out.truncate(total.max(per));
        out
    }

    /// Ratios, differences and densities from Wikidata numbers, computed in Rust (PLAN.md §9.5).
    /// Entities keep their order in the question, so "Russia than France" divides Russia by France.
    fn wikidata_calcs(lib: &Library, route: &Route) -> Vec<String> {
        let Some((_, wd)) = &lib.wikidata else { return vec![] };
        let lower = route.query.to_lowercase();
        let mut ents: Vec<(usize, &str, Vec<WdFact>)> = route
            .entities
            .iter()
            .filter_map(|e| {
                let facts = wd.facts(e.article.qid.as_deref()?, 60).ok()?;
                Some((lower.find(&e.surface.to_lowercase()).unwrap_or(usize::MAX), e.article.title.as_str(), facts))
            })
            .collect();
        ents.sort_by_key(|e| e.0);
        // (value, unit, property label) of the first fact whose label contains `frag`.
        let num = |facts: &[WdFact], frag: &str| {
            facts.iter().find(|f| f.label.contains(frag)).and_then(|f| {
                let v = f.value_num?;
                Some((v, f.value.strip_prefix(&format_num(v)).unwrap_or("").trim().to_string(), f.label.clone()))
            })
        };
        let round2 = |x: f64| (x * 100.0).round() / 100.0;
        let mut out = Vec::new();
        if lower.contains("density") {
            for (_, name, facts) in &ents {
                if let (Some((p, _, _)), Some((a, u, _))) = (num(facts, "population"), num(facts, "area"))
                    && a > 0.0
                {
                    out.push(format!("population density of {name} = {} ÷ {} {u} = {} per {u}", format_num(p), format_num(a), format_num(round2(p / a))));
                }
            }
        }
        const PROPS: &[(&[&str], &[&str])] = &[
            (&["taller", "tall", "height", "higher", "high"], &["elevation", "height"]),
            (&["larger", "bigger", "smaller", "large", "big", "area", "size"], &["area"]),
            (&["longer", "shorter", "long", "length"], &["length"]),
            (&["populous", "population", "people", "inhabitants"], &["population"]),
            (&["deeper", "deep", "depth"], &["depth"]),
            (&["heavier", "lighter", "mass", "weigh"], &["mass"]),
        ];
        if let [(_, a, fa), (_, b, fb), ..] = ents.as_slice() {
            'props: for (words, frags) in PROPS {
                if !words.iter().any(|w| lower.contains(w)) {
                    continue;
                }
                for frag in *frags {
                    if let (Some((x, ux, label)), Some((y, uy, _))) = (num(fa, frag), num(fb, frag))
                        && ux == uy
                        && y != 0.0
                    {
                        let unit = if ux.is_empty() { String::new() } else { format!(" {ux}") };
                        out.push(format!("{label} of {a} ÷ {label} of {b} = {}", format_num(round2(x / y))));
                        out.push(format!("{label} of {a} − {label} of {b} = {}{unit}", format_num(round2(x - y))));
                        // In words too: the model misreads a negative difference ("Is France bigger?" → "Yes").
                        let rel = if x > y { "greater than" } else if x < y { "less than" } else { "equal to" };
                        out.push(format!("{label} of {a} is {rel} {label} of {b}"));
                        break 'props;
                    }
                }
            }
        }
        out
    }

    pub fn ask(&self, req: &AskRequest, sink: &dyn EventSink) -> Result<AskOutcome> {
        let t0 = Instant::now();
        let ms = |t: Instant| t.elapsed().as_secs_f64() * 1000.0;
        let lib = self.library();
        let s = self.settings.read().unwrap().clone();
        let llm = self.llm();
        let mut rec = QueryRecord {
            ts_ms: telemetry::now_ms(),
            query: req.query.clone(),
            deep: req.deep,
            think: req.think,
            build: telemetry::BUILD.to_string(),
            thermal_headroom: *self.thermal.lock().unwrap(),
            model_id: llm.as_ref().map(|(_, l)| l.id()),
            ..Default::default()
        };
        let stage = |rec: &mut QueryRecord, st: Stage, detail: String| {
            rec.stages.push((format!("{st:?}"), ms(t0)));
            sink.event(Event::Stage { stage: st, detail });
        };
        let finish = |mut rec: QueryRecord, segments: Vec<Segment>, sources: Vec<SourceRef>, card: Card| {
            rec.total_ms = ms(t0);
            rec.peak_rss_mb = telemetry::peak_rss_mb();
            sink.event(Event::Stage { stage: Stage::Done, detail: String::new() });
            if let Some(p) = &self.cfg.telemetry_path {
                let _ = telemetry::append_jsonl(p, &rec);
            }
            let mut r = self.recent.lock().unwrap();
            r.push_back(rec.clone());
            while r.len() > RECENT {
                r.pop_front();
            }
            AskOutcome { kind: TurnKind::Question, card, sources, segments, record: rec }
        };

        // Small talk and reformat requests ("make it shorter") need no search, but a model to write the
        // reply. The empty card lets the app show it; a reformat also sends the last turn's sources, which
        // its citations use.
        let label = match (&self.enc, &llm) {
            (Some(enc), Some(_)) => Head::turn().classify(&enc.encode(&req.query)?),
            _ => "question",
        };
        let kind = match label {
            "reformat" if !req.history.is_empty() => TurnKind::Reformat,
            "chat" => TurnKind::Chat,
            _ => TurnKind::Question,
        };
        if let Some((_, l)) = llm.as_ref().filter(|_| kind != TurnKind::Question) {
            let card = Card { query: req.query.clone(), ..Default::default() };
            sink.event(Event::Card(card.clone()));
            stage(&mut rec, Stage::Writing, String::new());
            let (segments, sources) = match req.history.last().filter(|_| kind == TurnKind::Reformat) {
                Some(prev) => Self::reformat(l.as_ref(), prev, &req.query, s.max_answer_tokens, sink, &mut rec, t0)?,
                None => {
                    let mut raw = String::new();
                    rec.synthesis = Some(l.generate(prompts::CHAT_SYSTEM, &req.query, &GenParams::synthesis(48), &mut |t| {
                        raw.push_str(t);
                        !sink.cancelled()
                    })?);
                    // Small talk states no facts, so any number in the reply shows as unverified.
                    let segments = citations::check(raw.trim(), 0, &HashSet::new());
                    sink.event(Event::Answer { segments: segments.clone(), raw, done: true });
                    (segments, vec![])
                }
            };
            return Ok(AskOutcome { kind, ..finish(rec, segments, sources, card) });
        }

        // Stage 0-1: route, retrieve, instant card.
        stage(&mut rec, Stage::Searching, String::new());
        // Stage 0: the model rewrites the message into a standalone, typo-free search query using the last
        // turns (PLAN.md §9.1). The user's own words and the history still go to the answer.
        let rewritten = match llm.as_ref().filter(|_| s.rewrite) {
            Some((_, l)) => Self::rewrite(l.as_ref(), req, &mut rec),
            None => None,
        };
        let (search_text, topics) = match &rewritten {
            Some(rw) => (rw.query.clone(), rw.topics.clone()),
            None => (req.query.clone(), vec![]),
        };
        rec.topics = topics.clone();
        let mut route = route::route(&lib, &search_text, !req.history.is_empty(), self.rr.as_ref(), &topics);
        // The intent head reads the standalone question. Its best guess sets the cheap choices; only a
        // confident "calc" may start the slow LLM compute call.
        let emb = self.enc.as_ref().map(|e| e.encode(&route.query)).transpose()?;
        let intent = emb.as_deref().map_or("lookup", |e| Head::intent().best(e));
        let calc_sure = emb.as_deref().is_some_and(|e| Head::intent().classify(e) == "calc");
        // The rewrite model also marks comparisons, with the conversation in view ("is K2 much shorter?").
        route.compare |= intent == "compare" || rewritten.as_ref().is_some_and(|rw| rw.compare);
        route.complex |= route.compare || intent != "lookup";
        rec.intent = intent.to_string();
        rec.complex = route.complex;
        let raw_query = route.query.clone();
        // Follow-ups: a pronoun ("how many books does he have") or a question that names nothing of its own
        // ("a summary of Confessions" after asking about Augustine) searches with the previous turn's topic.
        // Only a pronoun also inherits the entity (card facts); otherwise the reranker judges the context.
        // Without the rewrite step, carry the previous turn's topic by rule.
        let follow_up = rewritten.is_none() && (route.needs_rewrite || (!req.history.is_empty() && route.entities.is_empty()));
        if follow_up && let Some(prev) = req.history.last() {
            let mut prev_entities = route::route(&lib, &prev.query, false, self.rr.as_ref(), &[]).entities;
            if prev_entities.is_empty() {
                // "Who invented the telephone?" has no name in it, but its answer does.
                let head: String = prev.answer.chars().take(300).collect();
                prev_entities = route::link_entities(&lib, &head);
                prev_entities.truncate(2);
            }
            let names: Vec<String> = prev_entities.iter().map(|e| e.article.title.clone()).collect();
            if !names.is_empty() {
                route.query = format!("{} {}", names.join(" "), route.query);
            } else if route.needs_rewrite {
                route.query = format!("{} {}", prev.query, route.query);
            }
            if route.needs_rewrite {
                route.entities.extend(prev_entities);
                route.entities.truncate(3);
            }
        }
        rec.search_query = route.query.clone();
        let retriever = Retriever { lib: &lib, enc: self.enc.as_ref(), rr: self.rr.as_ref(), s: &s.retrieval };
        let keep = s.retrieval.rerank_keep;
        let mut hits = retriever.retrieve(&route.query, &route.entities, keep, &mut rec.retrieval)?;
        let wd = Self::wikidata_facts(&lib, &route, 6);
        let unit_line = units::convert_query(&route.query);
        let card = {
            let top = card_passage(&hits, &route.entities).map(|h| {
                let facts = if s.use_cards { lib.packs[h.pack as usize].facts(h.pid).unwrap_or_default() } else { vec![] };
                let (text, from_cards) = if facts.is_empty() {
                    (h.passage.text.clone(), false)
                } else {
                    (facts.iter().map(|f| f.text.clone()).collect::<Vec<_>>().join("\n"), true)
                };
                let highlight = evidence::best_sentence(&text, &route.query);
                CardPassage { source: Self::source_ref(&lib, h, 0), text, highlight, from_cards }
            });
            Card {
                query: req.query.clone(),
                top,
                facts: wd.clone(),
                computed: unit_line.iter().cloned().collect(),
                sources: hits.iter().map(|h| Self::source_ref(&lib, h, 0)).collect(),
                entities: route.entities.iter().map(|e| e.article.title.clone()).collect(),
                card_ms: ms(t0),
                featured: None,
            }
        };
        rec.card_ms = card.card_ms;
        sink.event(Event::Card(card.clone()));

        // Stage 2b: featured snippet, the reader's answer span in the top passages (PLAN.md §9.3a).
        // Only simple lookups: why/how and multi-part questions have no single span. The written answer
        // still follows; skipping the LLM waits for a calibrated answerability check (gate G4).
        let mut card = card;
        if let Some(reader) = self.reader.as_ref().filter(|_| !route.complex) {
            let t = Instant::now();
            let best = hits
                .iter()
                .take(3)
                .filter_map(|h| reader.read(&route.query, &h.passage.text).ok().flatten().map(|s| (s, h)))
                .max_by(|a, b| a.0.margin.total_cmp(&b.0.margin));
            rec.reader_ms = Some(ms(t));
            // Agreement (PLAN.md §9.3a): the span must appear in two passages or in a Wikidata fact.
            // One passage alone can name the wrong claimant ("John W. Starr" invented a light bulb too).
            let agrees = |span: &str| {
                let s = span.to_lowercase();
                // A lone common word ("two") agrees with everything; a name or a number is specific.
                let specific = span.chars().any(|c| c.is_uppercase() || c.is_ascii_digit());
                specific
                    && (hits.iter().filter(|h| h.passage.text.to_lowercase().contains(&s)).count() >= 2
                        || card.facts.iter().any(|f| f.value.to_lowercase().contains(&s)))
            };
            if let Some((span, h)) = best.filter(|(s, _)| s.margin >= FEATURED_MIN_MARGIN && agrees(&s.text)) {
                rec.featured_margin = Some(span.margin);
                let sentence = crate::text::sentences(&h.passage.text).into_iter().find(|s| s.contains(&span.text)).unwrap_or(&span.text).to_string();
                if let Some(top) = card.top.as_mut().filter(|t| t.source.passage_id == h.pid && t.source.pack_id == lib.packs[h.pack as usize].manifest.pack_id && !t.from_cards) {
                    top.highlight = sentence.clone();
                }
                card.featured = Some(Featured { text: span.text, sentence, margin: span.margin, source: Self::source_ref(&lib, h, 0) });
                sink.event(Event::Card(card.clone()));
            }
        }

        let Some((_, llm)) = llm.filter(|_| !sink.cancelled()) else {
            let sources = hits.iter().enumerate().map(|(i, h)| Self::source_ref(&lib, h, i as u32 + 1)).collect();
            return Ok(finish(rec, vec![], sources, card));
        };
        if let Some(h) = rec.thermal_headroom {
            let n = if h > 0.85 { s.llm_threads.saturating_sub(1).max(2) } else { s.llm_threads };
            llm.set_threads(n);
            rec.threads = Some(n);
        }

        // Stage 2: subqueries. A planner call costs ~19 s on the Pixel CPU (and often returns no subqueries),
        // so it runs only in deep mode and for follow-ups that entity carry-over could not resolve.
        // Comparisons get one template search per entity instead (PLAN.md §9.1).
        let mut question = route.query.clone();
        let mut plan = Plan::default();
        let wants_plan = req.deep || (route.needs_rewrite && route.entities.is_empty());
        if !(s.use_planner && wants_plan) && route.compare && route.entities.len() >= 2 {
            let attr = route::attribute(&route);
            // The user's words, not the linked title: a wrong link ("Nile (band)") must not steer the search.
            plan.subqueries = route.entities.iter().map(|e| format!("{} {attr}", e.surface).trim().to_string()).collect();
            rec.subqueries = plan.subqueries.clone();
        }
        if s.use_planner && wants_plan {
            stage(&mut rec, Stage::Planning, String::new());
            let turns: String = req.history.iter().rev().take(2).rev().map(|t| format!("Q: {} A: {} ", t.query, trunc(&t.answer, 200))).collect();
            let titles: String = hits
                .iter()
                .map(|h| format!("{} ({})", h.article.title, h.article.oneliner.as_deref().map(|o| trunc(o, 100)).unwrap_or_default()))
                .collect::<Vec<_>>()
                .join("; ");
            let mut out = String::new();
            let st = llm.generate(
                prompts::PLANNER_SYSTEM,
                &prompts::planner_user(&turns, &raw_query, &titles),
                &GenParams::json(160, prompts::PLANNER_GRAMMAR),
                &mut |t| {
                    out.push_str(t);
                    !sink.cancelled()
                },
            )?;
            rec.planner = Some(st);
            rec.planner_used = true;
            if let Some(p) = parse_json::<Plan>(&out) {
                plan = p;
            }
            if !plan.standalone_query.trim().is_empty() {
                question = plan.standalone_query.trim().to_string();
            }
            plan.subqueries.retain(|q| !q.trim().is_empty());
            plan.subqueries.truncate(3);
            rec.subqueries = plan.subqueries.clone();
        }
        if !plan.subqueries.is_empty() || question != route.query {
            let mut pool: Vec<Hit> = if route.needs_rewrite { Vec::new() } else { retriever.fused(&route.query, &route.entities, &mut rec.retrieval)? };
            let mut queries = plan.subqueries.clone();
            if route.needs_rewrite || queries.is_empty() {
                queries.push(question.clone());
            }
            for q in &queries {
                let ents = route::link_entities(&lib, q);
                let mut st = Default::default();
                for h in retriever.fused(q, &ents, &mut st)? {
                    if !pool.iter().any(|p| p.pack == h.pack && p.pid == h.pid) {
                        pool.push(h);
                    }
                }
            }
            pool.sort_by(|a, b| b.fused.total_cmp(&a.fused));
            pool.truncate(s.retrieval.fuse_keep * 2);
            let reranked = retriever.rerank(&question, pool, 24, &mut rec.retrieval)?;
            let must: Vec<(u8, u32)> = if route.compare { route.entities.iter().map(|e| (e.pack, e.article.id)).collect() } else { vec![] };
            hits = select_diverse(&reranked, if req.deep { 16 } else { 10 }, 3, &must);
        }
        if sink.cancelled() {
            return Ok(finish(rec, vec![], vec![], card));
        }

        // Stage 3: evidence.
        let budget = if req.deep { s.deep_evidence_tokens } else { s.evidence_tokens };
        if let (Some(gap), Some(best)) = (s.evidence_score_gap, hits.iter().filter_map(|h| h.rerank).reduce(f32::max)) {
            let mut kept = 0;
            hits.retain(|h| {
                kept += 1;
                kept <= 3 || h.rerank.is_none_or(|r| r >= best - gap)
            });
        }
        let mut ev: Evidence = evidence::assemble(&lib, &hits, &question, budget, wd);
        if !s.use_cards {
            for src in &mut ev.sources {
                if src.from_cards {
                    src.shown = evidence::sentence_window(&src.hit.passage.text, &crate::text::terms(&question).into_iter().collect(), 120);
                    src.from_cards = false;
                }
            }
        }
        if let Some(u) = unit_line {
            ev.add_computed(u);
        }
        stage(&mut rec, Stage::Reading, ev.sources.len().to_string());

        // Stage 3b: compute. Wikidata values first (no LLM); the LLM compute call re-reads the whole evidence
        // (10–13 s on the Pixel), so it runs only for an explicit calculation that Wikidata cannot answer.
        let wants_numbers = plan.needs_numbers || plan.intent == "calc" || intent == "calc";
        let calcs = if wants_numbers || route.compare || route.entities.len() >= 2 { Self::wikidata_calcs(&lib, &route) } else { vec![] };
        let deterministic = !calcs.is_empty();
        for c in calcs {
            ev.add_computed(c);
        }
        if s.use_compute && (calc_sure || plan.needs_numbers || plan.intent == "calc") && !deterministic && !ev.numbers.is_empty() {
            stage(&mut rec, Stage::Computing, String::new());
            let mut out = String::new();
            let st = llm.generate(
                prompts::COMPUTE_SYSTEM,
                &prompts::compute_user(&ev.render(&lib), &question),
                &GenParams::json(200, prompts::COMPUTE_GRAMMAR),
                &mut |t| {
                    out.push_str(t);
                    !sink.cancelled()
                },
            )?;
            rec.compute = Some(st);
            if let Some(c) = parse_json::<Calcs>(&out) {
                for calc in c.calcs.into_iter().take(4) {
                    let lits = calc::literals(&calc.expr);
                    if lits.is_empty() || !lits.iter().all(|l| ev.numbers.contains(l.trim_end_matches(".0")) || ev.numbers.contains(l)) {
                        continue;
                    }
                    if let Ok(v) = calc::eval(&calc.expr) {
                        ev.add_computed(format!("{} = {}", calc.label.trim(), format_num(v)));
                    }
                }
            }
        }

        let sources: Vec<SourceRef> = ev.sources.iter().map(|src| Self::source_ref(&lib, &src.hit, src.n)).collect();
        sink.event(Event::Sources(sources.clone()));
        rec.n_sources = sources.len();

        // Stage 4: synthesis with the cached system prefix (after the reasoning, in thinking mode).
        stage(&mut rec, if req.think { Stage::Thinking } else { Stage::Writing }, String::new());
        let rendered = ev.render(&lib);
        rec.evidence_tokens = crate::text::approx_tokens(&rendered);
        ev.numbers.extend(crate::text::numbers(&lib.snapshot_date()));
        let system = prompts::synthesis_system(&lib.snapshot_date());
        // Keep the user's own words in front of the model; a rewrite from a small planner can drift.
        let asked = if raw_query != question && !req.history.is_empty() { format!("{} ({question})", req.query.trim()) } else { question.clone() };
        // The last exchange, so a follow-up builds on it. Its citations point at another turn's sources.
        let earlier = req.history.last().map(|t| {
            let plain = citations::plain_text(&citations::check(&t.answer, 0, &HashSet::new()));
            (t.query.as_str(), trunc(&crate::text::sentences(&plain).into_iter().take(2).collect::<Vec<_>>().join(" "), 300))
        });
        let user = prompts::synthesis_user(&rendered, &asked, earlier.as_ref().map(|(q, a)| (*q, a.as_str())));
        let n = ev.sources.len() as u32;
        let max = if req.deep { s.deep_max_answer_tokens } else { s.max_answer_tokens };
        let mut params = GenParams::synthesis(max);
        params.think_budget = req.think.then_some(s.think_budget);
        let mut raw = String::new();
        let mut first: Option<f64> = None;
        let mut thought_done = !req.think;
        let mut closes = 0;
        let st: GenStats = llm.generate(&system, &user, &params, &mut |t| {
            raw.push_str(t);
            let (thought, answer) = split_thinking(&raw);
            if !thought_done {
                thought_done = raw.contains("</think>");
                closes = raw.matches("</think>").count();
                sink.event(Event::Thinking { text: thought.trim().to_string(), done: thought_done });
                if thought_done {
                    stage(&mut rec, Stage::Writing, String::new());
                }
                return !sink.cancelled();
            }
            if req.think && raw.matches("</think>").count() > closes {
                // The model kept reasoning after the forced close: that text moves back to the reasoning.
                closes = raw.matches("</think>").count();
                sink.event(Event::Thinking { text: thought.trim().to_string(), done: true });
            }
            if answer.trim().is_empty() {
                return !sink.cancelled();
            }
            if first.is_none() {
                first = Some(ms(t0));
            }
            sink.event(Event::Answer { segments: citations::check(strip_echo(answer, &[&question, &raw_query]), n, &ev.numbers), raw: answer.to_string(), done: false });
            !sink.cancelled()
        })?;
        let mut text = split_thinking(&raw).1.to_string();
        if st.truncated {
            text = complete_part(&text).to_string();
        }
        rec.ttft_ms = first;
        rec.synthesis = Some(st);
        let segments = citations::check(strip_echo(text.trim(), &[&question, &raw_query]), n, &ev.numbers);
        rec.unverified_sentences = segments.iter().filter(|s| matches!(s, Segment::Unverified(_))).count();
        sink.event(Event::Answer { segments: segments.clone(), raw: text.clone(), done: true });
        let mut card = card;
        card.computed = ev.computed.clone();
        Ok(finish(rec, segments, sources, card))
    }
}

/// Drop a first line that only repeats the question (some models echo it before answering).
fn strip_echo<'a>(text: &'a str, questions: &[&str]) -> &'a str {
    let key = |s: &str| s.chars().filter(|c| c.is_alphanumeric()).flat_map(char::to_lowercase).collect::<String>();
    let t = text.trim_start();
    // The echo ends at the first line break or question mark, whichever comes first.
    let end = t.find(['\n', '?']).map(|i| i + 1).unwrap_or(t.len());
    let (first, rest) = t.split_at(end);
    if questions.iter().any(|q| !q.is_empty() && key(first) == key(q)) { rest.trim_start() } else { text }
}

fn trunc(s: &str, n: usize) -> String {
    if s.chars().count() <= n { s.to_string() } else { format!("{}…", s.chars().take(n).collect::<String>()) }
}

/// Split a thinking-mode stream `<think>reasoning</think>answer` into (reasoning, answer).
/// Without the `<think>` opener the whole text is the answer. The last `</think>` counts: a model that keeps
/// reasoning after a forced close writes its own later. Text after a stray `<think>` in the answer is reasoning too.
fn split_thinking(raw: &str) -> (&str, &str) {
    let (thought, answer) = match raw.strip_prefix("<think>") {
        Some(rest) => match rest.rfind("</think>") {
            Some(i) => (&rest[..i], &rest[i + "</think>".len()..]),
            None => (rest, ""),
        },
        None => ("", raw),
    };
    (thought, answer.split("<think>").next().unwrap_or(""))
}

/// An answer cut off at the token limit, without its unfinished last sentence or list line. Keeps everything
/// when the cut would remove more than two thirds.
fn complete_part(text: &str) -> &str {
    let t = text.trim_end();
    let b = t.as_bytes();
    let mut end = t.rfind('\n').unwrap_or(0);
    for (i, c) in t.char_indices().rev() {
        if matches!(c, '.' | '!' | '?') {
            // A sentence end may carry citations: "… light.[2][3]".
            let mut j = i + 1;
            while j < b.len() && b[j] == b'[' {
                match t[j..].find(']') {
                    Some(k) => j += k + 1,
                    None => break,
                }
            }
            if j == b.len() || b[j].is_ascii_whitespace() {
                end = end.max(j);
                break;
            }
        }
    }
    if end * 3 < t.len() { text } else { t[..end].trim_end() }
}

/// The card's passage: the named entity's article when it is among the hits; otherwise the best passage
/// of the article with the strongest set of hits (best score plus half of the rest). Relevant passages
/// score within tenths of each other, so the single top one is often a page that only mentions the topic.
fn card_passage<'a>(hits: &'a [Hit], entities: &[crate::route::Entity]) -> Option<&'a Hit> {
    // The article the question names ("St Augustine" → Augustine of Hippo) wins when it is among the hits.
    if let Some(h) = entities.first().and_then(|e| hits.iter().find(|h| h.pack == e.pack && h.article.id == e.article.id)) {
        return Some(h);
    }
    let mut by_article: Vec<((u8, u32), f32, &Hit)> = Vec::new();
    for h in hits {
        match by_article.iter_mut().find(|(k, _, _)| *k == (h.pack, h.article.id)) {
            Some((_, sum, _)) => *sum += 0.5 * h.score(),
            None => by_article.push(((h.pack, h.article.id), h.score(), h)),
        }
    }
    by_article.into_iter().max_by(|a, b| a.1.total_cmp(&b.1)).map(|(_, _, h)| h)
}

impl Engine {
    /// Rewrite the last answer as asked. Valid citations are the ones it used, and its numbers are the
    /// only verified ones: a reformat adds no facts.
    fn reformat(
        llm: &dyn LlmBackend,
        prev: &Turn,
        request: &str,
        max_tokens: u32,
        sink: &dyn EventSink,
        rec: &mut QueryRecord,
        t0: Instant,
    ) -> Result<(Vec<Segment>, Vec<SourceRef>)> {
        let n = citations::check(&prev.answer, u32::MAX, &HashSet::new())
            .iter()
            .filter_map(|s| if let Segment::Cite(n) = s { Some(*n) } else { None })
            .max()
            .unwrap_or(0);
        let numbers: HashSet<String> = crate::text::numbers(&prev.answer).into_iter().collect();
        sink.event(Event::Sources(prev.sources.clone()));
        let mut raw = String::new();
        let st = llm.generate(prompts::REFORMAT_SYSTEM, &prompts::reformat_user(&prev.query, &prev.answer, request), &GenParams::synthesis(max_tokens), &mut |t| {
            raw.push_str(t);
            if rec.ttft_ms.is_none() && !raw.trim().is_empty() {
                rec.ttft_ms = Some(t0.elapsed().as_secs_f64() * 1000.0);
            }
            sink.event(Event::Answer { segments: citations::check(&raw, n, &numbers), raw: raw.clone(), done: false });
            !sink.cancelled()
        })?;
        rec.synthesis = Some(st);
        let segments = citations::check(raw.trim(), n, &numbers);
        sink.event(Event::Answer { segments: segments.clone(), raw, done: true });
        Ok((segments, prev.sources.clone()))
    }

    /// One short, greedy call: the message plus the last two turns in; whether it compares things, a
    /// standalone search query and the Wikipedia titles of its topics out. `None` when the output is
    /// unusable (the raw message is searched).
    fn rewrite(llm: &dyn LlmBackend, req: &AskRequest, rec: &mut QueryRecord) -> Option<Rewrite> {
        let turns: String =
            req.history.iter().rev().take(2).rev().map(|t| format!("User: {}\nAssistant: {}\n", t.query, trunc(&t.answer, 200))).collect();
        let mut out = String::new();
        let st = llm
            .generate(prompts::REWRITE_SYSTEM, &prompts::rewrite_user(&turns, &req.query), &GenParams::greedy(80), &mut |t| {
                out.push_str(t);
                // Stop once the "Topics:" line is complete.
                !out.split_once("Topics:").is_some_and(|(_, rest)| rest.contains('\n'))
            })
            .ok()?;
        rec.rewrite = Some(st);
        let field = |name: &str| out.lines().find_map(|l| l.trim().strip_prefix(name)).map(|v| v.trim().trim_matches('"').trim().to_string());
        // The model sometimes drops the "Query:" label; the first other line is the query then.
        let first = out
            .lines()
            .map(str::trim)
            .find(|l| !l.is_empty() && !l.starts_with("Topics:") && !l.starts_with("Kind:"))
            .map(|l| l.trim_matches('"').to_string());
        // One query only: the model sometimes lists variants separated by "; ".
        let query = field("Query:").or(first).map(|q| q.split("; ").next().unwrap_or("").trim().to_string());
        let query = query.filter(|q| !q.is_empty() && q.len() <= 2 * req.query.len() + 80)?;
        let topics = field("Topics:")
            .filter(|t| !t.eq_ignore_ascii_case("none"))
            .map(|t| t.split(';').map(|x| x.trim().to_string()).filter(|x| !x.is_empty()).take(3).collect())
            .unwrap_or_default();
        let compare = field("Kind:").is_some_and(|k| k.to_lowercase().starts_with("compare"));
        Some(Rewrite { compare, query, topics })
    }
}
