//! The orchestrator: Plan → Retrieve → Compute → Synthesize, with streaming events.

use crate::citations::{self, Segment};
use crate::encoders::{Head, QueryEncoder, Reader, Reranker};
use crate::evidence::{self, Evidence};
use crate::library::{Library, ModelPack};
use crate::llm::{GenParams, GenStats, LlmBackend};
use crate::pack::{ModelRole, normalize_title};
use crate::prompts;
use crate::retrieval::{Hit, RetrievalSettings, Retriever, select_diverse};
use crate::route::{self, Route};
use crate::telemetry::{self, QueryRecord};
use crate::tools::{calc, units, wikidata::PropScore, wikidata::WdFact, wikidata::format_num, wikidata::prop_scores};
use anyhow::{Context, Result, anyhow, ensure};
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
    /// Pack ids this question searches (retrieval, entity linking, Wikidata). Empty: every enabled pack.
    pub packs: Vec<String>,
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
    /// The pack's license and the article's web page, for the source list. Empty when unknown.
    #[serde(default)]
    pub license: String,
    #[serde(default)]
    pub source_url: String,
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

/// Intent-head probability of "compare" at which a question that names two things compares them, and a
/// follow-up that names one thing is compared with the last topic: "Is France bigger?" scores 0.38, "How big
/// is France?" 0.03. "Is Saturn bigger than Neptune?" scores 0.38 though it reads more like calc (0.45).
const COMPARE_PROB: f32 = 0.25;

/// Follow-up-head probability at which a message that names nothing continues a last question that named
/// exactly one thing ("How many books did he write?" after "Who is Stephen King?"). New topics after such a
/// question scored up to 0.29 in the conversation set; the head's own bar is 0.5.
const FOLLOW_UP_LOW: f32 = 0.35;

/// `wikidata::prop_scores` score at which the question asks about a property. In a 77-question check, the
/// asked property of 43 of 47 answerable questions was among an entity's 3 best facts at this score or above.
const FACT_RELEVANCE: f32 = 0.10;
/// The same for the best passage's entity, which the question may not be about ("part of" scores 0.105 for
/// "When was the US created?" and the article "History of the United States").
const GUESSED_FACT_RELEVANCE: f32 = 0.15;
/// Item-valued facts per entity that a Wikidata hop follows, best match first.
const HOP_BRIDGES: usize = 8;
/// Reranker score at which a hop's chain of facts is relevant. In a check on 155 seed and conversation turns,
/// the three right chains scored 11.0 to 11.6, the best wrong one 10.0 ("2016 United States presidential
/// election — country: United States. United States — head of government: Donald Trump").
const HOP_RELEVANCE: f32 = 10.5;
/// Reranker margin (logits) by which the answer before the last must read closer to a follow-up to be its topic.
/// On 39 held-out and phone follow-ups whose topic was in only one of the two turns, it picked the older turn
/// for 7 of 10 that needed it and wrongly for 2 of 29 that did not. At 1.0 "Compare it with Canada" (1.09) left Australia.
const REFERENT_MARGIN: f32 = 1.5;
/// Asked-about facts per entity in the evidence.
const FACTS_PER_ENTITY: usize = 3;
/// Numbers every compared entity has, in the evidence of a comparison.
const SHARED_NUMBERS: usize = 3;
/// `wikidata::prop_scores` score of the best quantity at which a question without its names asks for a number
/// ("which was bigger, the or the ?" 0.189, "compare and" 0.120). Set on 26 comparisons; on 22 others it let
/// through 12 of 12 that ask for a number and 1 of 10 that do not.
const NUMBER_ASKED: f32 = 0.16;

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
        let route = route::route(&lib, query, self.rr.as_ref(), &[], &[]);
        let retriever = Retriever { lib: &lib, enc: self.enc.as_ref(), rr: self.rr.as_ref(), s: &s.retrieval, scope: &[] };
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

    /// Switch a knowledge or Wikidata pack on or off, then reload. Keeps the loaded LLM.
    pub fn set_pack_enabled(&self, pack_id: &str, enabled: bool) -> Result<()> {
        let lib = self.library();
        let known = lib.packs.iter().map(|p| &p.manifest).chain(lib.wikidata.iter().map(|(m, _)| m)).chain(&lib.disabled).any(|m| m.pack_id == pack_id);
        ensure!(known, "{pack_id} is not an installed knowledge or Wikidata pack");
        Library::set_enabled(&self.cfg.library_dir, pack_id, enabled)?;
        self.reload_library()
    }

    /// Index a document the user added (text per page) as a knowledge pack and reload the library.
    /// Keeps the loaded LLM. `progress(done, total)` counts embedded passages. Returns the pack id.
    pub fn add_document(&self, title: &str, pages: &[String], progress: &mut dyn FnMut(usize, usize)) -> Result<String> {
        let enc = self.enc.as_ref().ok_or_else(|| anyhow!("the search model is not installed"))?;
        let id = crate::userdoc::build(&self.cfg.library_dir, title, pages, enc, progress)?;
        self.reload_library()?;
        Ok(id)
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
        let (threads, rewrite) = {
            let s = self.settings.read().unwrap();
            (s.llm_threads, s.rewrite)
        };
        let llm = loader(mp, threads)?;
        Self::warm_up(llm.as_ref(), &lib.snapshot_date(), rewrite);
        *self.llm.write().unwrap() = Some((role, llm));
        Ok(())
    }

    /// Prefill the fixed answer prompt once, and the rewrite prompt when that step is on, so the first
    /// question does not pay for them (the backend caches a system prompt's state after its first use, and
    /// with a state directory loads the answer prompt's state from disk instead of prefilling it).
    /// Seconds on a phone CPU.
    pub fn warm_up(llm: &dyn LlmBackend, snapshot_date: &str, rewrite: bool) {
        let one = GenParams::greedy(1);
        if rewrite {
            let _ = llm.generate(prompts::REWRITE_SYSTEM, "Message: hello", &one, &mut |_| false);
        }
        let answer = GenParams { keep_prefix: true, ..one };
        let _ = llm.generate(&prompts::synthesis_system(snapshot_date, true), "Question: hello", &answer, &mut |_| false);
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
            license: pack.license.clone(),
            source_url: crate::pack::source_url(&pack.pack_id, &h.article.title, h.article.url_title.as_deref().unwrap_or_default()).unwrap_or_default(),
        }
    }

    /// The history turn a follow-up refers to: the last one, unless the one before reads clearly closer to the
    /// message ("Was he also a singer?" two turns after Louis Armstrong, with a sum in between). The reranker reads
    /// the message against the first sentence of each answer. An expand request always means the last turn.
    fn referent(&self, history: &[Turn], message: &str, expand: bool) -> usize {
        let last = history.len().saturating_sub(1);
        let Some(rr) = self.rr.as_ref().filter(|_| !expand && history.len() >= 2) else { return last };
        let first = |t: &Turn| {
            let plain = citations::plain_text(&citations::check(&t.answer, 0, &HashSet::new()));
            crate::text::sentences(&plain).into_iter().next().unwrap_or_default().to_string()
        };
        match rr.score(message, &[first(&history[last - 1]), first(&history[last])]) {
            Ok(s) if s[0] > s[1] + REFERENT_MARGIN => last - 1,
            _ => last,
        }
    }

    /// A Wikidata entity in the source list: its facts are the snippet, and the QID is the passage and article id.
    fn wikidata_ref(lib: &Library, w: &evidence::WdSource) -> SourceRef {
        let (pack_id, pack_title, license) = lib.wikidata.as_ref().map(|(m, _)| (m.pack_id.clone(), m.title.clone(), m.license.clone())).unwrap_or_default();
        let f = &w.facts[0];
        SourceRef {
            n: w.n,
            pack_id,
            pack_title,
            passage_id: f.qid_num(),
            article_id: f.qid_num(),
            title: f.entity.clone(),
            section: String::new(),
            snippet: w.shown.join("; ").chars().take(160).collect(),
            score: 0.0,
            license,
            source_url: format!("https://www.wikidata.org/wiki/{}", f.qid),
        }
    }

    /// The Wikidata pack, unless a pack scope leaves it out.
    fn wikidata<'a>(lib: &'a Library, scope: &[String]) -> Option<&'a crate::tools::wikidata::WikidataDb> {
        let (m, wd) = lib.wikidata.as_ref()?;
        (scope.is_empty() || scope.contains(&m.pack_id)).then_some(wd)
    }

    /// The card's facts and the evidence's facts. The card shows up to `total` facts of the named entities,
    /// those the question asks about first. The evidence keeps only the asked-about facts and, for a
    /// comparison, the numbers every entity has. `scores` are `wikidata::prop_scores` of the question.
    fn wikidata_facts(lib: &Library, route: &Route, top: Option<&Hit>, scores: &[PropScore], compare: bool, total: usize, scope: &[String]) -> (Vec<WdFact>, Vec<WdFact>) {
        let Some(wd) = Self::wikidata(lib, scope) else { return (vec![], vec![]) };
        let mut qids: Vec<&str> = route.entities.iter().filter_map(|e| e.article.qid.as_deref()).collect();
        let named = qids.len();
        // The best passage's article joins with the facts the question clearly asks about: the question may name
        // nothing, or name something else ("the inventor of the World Wide Web").
        if let Some(q) = top.and_then(|h| h.article.qid.as_deref()).filter(|q| !qids.contains(q) && !scores.is_empty()) {
            qids.push(q);
        }
        if qids.is_empty() {
            return (vec![], vec![]);
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
        let score = |f: &WdFact| scores.iter().find(|s| s.id == f.pid).map(|s| s.score);
        let asked = |f: &WdFact| score(f).filter(|s| *s >= FACT_RELEVANCE);
        let bar = |i: usize| if i < named { FACT_RELEVANCE } else { GUESSED_FACT_RELEVANCE };
        let shared = |f: &WdFact| all[..named].iter().all(|o| o.iter().any(|g| g.pid == f.pid));
        let mut card = Vec::new();
        let mut evidence = Vec::new();
        let per = (total / named.max(1)).max(3);
        // A comparison lines up the numbers every named entity has, best match first.
        let mut numbers: Vec<&WdFact> = if named >= 2 && compare { all[0].iter().filter(|f| f.value_num.is_some() && shared(f)).collect() } else { vec![] };
        numbers.sort_by(|a, b| score(b).unwrap_or(f32::MIN).total_cmp(&score(a).unwrap_or(f32::MIN)));
        numbers.truncate(SHARED_NUMBERS);
        for (i, facts) in all.iter().enumerate() {
            let mut facts = facts.clone();
            // Asked-about facts first (best first), then properties every entity has (so a comparison lines
            // up), then the pack's priority order (stable sort).
            facts.sort_by(|a, b| {
                let key = |f: &WdFact| (-asked(f).unwrap_or(f32::NEG_INFINITY), !shared(f));
                let (x, y) = (key(a), key(b));
                x.0.total_cmp(&y.0).then(x.1.cmp(&y.1))
            });
            let picked: Vec<&WdFact> = if scores.is_empty() {
                facts.iter().take(per).collect()
            } else {
                let mut p: Vec<&WdFact> = facts.iter().filter(|f| score(f).is_some_and(|s| s >= bar(i))).take(FACTS_PER_ENTITY).collect();
                if i < named {
                    let lined_up: Vec<&WdFact> = facts.iter().filter(|f| numbers.iter().any(|n| n.pid == f.pid) && !p.iter().any(|g| g.pid == f.pid)).collect();
                    p.extend(lined_up);
                }
                p
            };
            evidence.extend(picked.into_iter().cloned());
            if i < named {
                card.extend(facts.into_iter().take(per));
            } else if named == 0 {
                card.extend(evidence.iter().cloned());
            }
        }
        card.truncate(total.max(per));
        if scores.is_empty() {
            evidence = card.clone();
        }
        (card, evidence)
    }

    /// The property the question asks about most, when no entity has it, from an entity one item-valued fact
    /// away: "the country that hosted the 1964 Summer Olympics" gives "country: Japan", then Japan's "highest
    /// point". Returns the bridge fact and the asked fact when the reranker finds the chain relevant.
    fn wikidata_hop(lib: &Library, rr: &Reranker, route: &Route, top: Option<&Hit>, scores: &[PropScore], scope: &[String]) -> Result<Vec<WdFact>> {
        let Some(wd) = Self::wikidata(lib, scope) else { return Ok(vec![]) };
        let Some(want) = scores.iter().filter(|s| !s.id.contains('/')).max_by(|a, b| a.score.total_cmp(&b.score)).filter(|s| s.score >= FACT_RELEVANCE) else {
            return Ok(vec![]);
        };
        let mut qids: Vec<&str> = route.entities.iter().filter_map(|e| e.article.qid.as_deref()).collect();
        if let Some(q) = top.and_then(|h| h.article.qid.as_deref()).filter(|q| !qids.contains(q)) {
            qids.push(q);
        }
        let raw: Vec<Vec<WdFact>> = qids.iter().map(|q| wd.facts(q, 40).unwrap_or_default()).collect();
        if raw.iter().flatten().any(|f| f.pid == want.id) {
            return Ok(vec![]);
        }
        let score = |f: &WdFact| scores.iter().find(|s| s.id == f.pid).map_or(f32::MIN, |s| s.score);
        let mut chains: Vec<[WdFact; 2]> = Vec::new();
        'entities: for facts in &raw {
            // A class's facts do not hold for its members.
            let mut bridges: Vec<&WdFact> = facts.iter().filter(|f| f.value_num.is_none() && f.value != f.entity && !matches!(f.pid.as_str(), "P31" | "P279")).collect();
            bridges.sort_by(|a, b| score(b).total_cmp(&score(a)));
            for b in bridges.into_iter().take(HOP_BRIDGES) {
                for v in b.value.split(", ") {
                    let found = lib.scoped(scope).filter_map(|(_, p)| p.meta.lookup_title(&normalize_title(v)).ok().flatten()).max_by_key(|a| a.popularity);
                    let Some(q) = found.and_then(|a| a.qid).filter(|q| *q != b.qid) else { continue };
                    let asked = wd.facts(&q, 100).unwrap_or_default().into_iter().find(|f| f.pid == want.id && !f.value.split(", ").any(|v| v == b.entity));
                    if let Some(f) = asked {
                        chains.push([b.clone(), f]);
                        continue 'entities;
                    }
                }
            }
        }
        if chains.is_empty() {
            return Ok(vec![]);
        }
        let docs: Vec<String> = chains.iter().map(|[b, f]| format!("{}. {}", b.line(), f.line())).collect();
        let rel = rr.score(&route.query, &docs)?;
        let best = (0..chains.len()).max_by(|&a, &b| rel[a].total_cmp(&rel[b])).filter(|&i| rel[i] >= HOP_RELEVANCE);
        Ok(best.map(|i| chains.swap_remove(i).to_vec()).unwrap_or_default())
    }

    /// Ratios, differences and derived values (population density) from Wikidata numbers, computed in Rust,
    /// for the number the question asks about. Entities keep their order in the question, so "Russia than
    /// France" divides Russia by France.
    fn wikidata_calcs(lib: &Library, route: &Route, scores: &[PropScore], scope: &[String]) -> Vec<String> {
        let Some(wd) = Self::wikidata(lib, scope) else { return vec![] };
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
        let pid_num = |facts: &[WdFact], pid: &str| {
            facts.iter().find(|f| f.pid == pid).and_then(|f| {
                let v = f.value_num?;
                Some((v, f.value.strip_prefix(&format_num(v)).unwrap_or("").trim().to_string()))
            })
        };
        // (value, unit) of a property, or of a ratio of two ("P1082/P2046").
        let value = |facts: &[WdFact], id: &str| match id.split_once('/') {
            Some((a, b)) => {
                let ((x, _), (y, uy)) = (pid_num(facts, a)?, pid_num(facts, b)?);
                (y > 0.0).then(|| (x / y, format!("per {uy}").trim().to_string()))
            }
            None => pid_num(facts, id),
        };
        // Each entity's best-matching number. A comparison needs the same one for both: "How far is Mars from
        // the Sun?" matches the distance, which only Mars has, so the areas are not compared.
        let best = |facts: &[WdFact]| scores.iter().filter(|s| value(facts, s.id).is_some()).max_by(|a, b| a.score.total_cmp(&b.score));
        let round2 = |x: f64| (x * 100.0).round() / 100.0;
        let mut out = Vec::new();
        let Some(prop) = ents.first().and_then(|(_, _, f)| best(f)).filter(|p| p.score >= FACT_RELEVANCE) else { return out };
        let label = prop.label;
        if let Some((pa, pb)) = prop.id.split_once('/') {
            for (_, name, facts) in &ents {
                if let (Some((x, ux)), Some((y, uy)), Some((v, u))) = (pid_num(facts, pa), pid_num(facts, pb), value(facts, prop.id)) {
                    let ux = if ux.is_empty() { String::new() } else { format!(" {ux}") };
                    out.push(format!("{label} of {name} = {}{ux} ÷ {} {uy} = {} {u}", format_num(x), format_num(y), format_num(round2(v))));
                }
            }
        }
        if let [(_, a, fa), (_, b, fb), ..] = ents.as_slice()
            && best(fb).is_some_and(|p| p.id == prop.id)
            && let (Some((x, ux)), Some((y, uy))) = (value(fa, prop.id), value(fb, prop.id))
            && ux == uy
            && y != 0.0
        {
            let unit = if ux.is_empty() { String::new() } else { format!(" {ux}") };
            out.push(format!("{label} of {a} ÷ {label} of {b} = {}", format_num(round2(x / y))));
            out.push(format!("{label} of {a} − {label} of {b} = {}{unit}", format_num(round2(x - y))));
            // In words too: the model misreads a negative difference ("Is France bigger?" → "Yes").
            let rel = if x > y { "greater than" } else if x < y { "less than" } else { "equal to" };
            out.push(format!("{label} of {a} is {rel} {label} of {b}"));
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
        // its citations use. An expand request ("tell me more") is a question about the last topic.
        let label = match &self.enc {
            Some(enc) => Head::turn().classify(&enc.encode(&req.query)?),
            None => "question",
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
        // turns. The user's own words and the history still go to the answer.
        let rewritten = match llm.as_ref().filter(|_| s.rewrite) {
            Some((_, l)) => Self::rewrite(l.as_ref(), req, &mut rec),
            None => None,
        };
        let (search_text, topics) = match &rewritten {
            Some(rw) => (rw.query.clone(), rw.topics.clone()),
            None => (req.query.clone(), vec![]),
        };
        rec.topics = topics.clone();
        let mut route = route::route(&lib, &search_text, self.rr.as_ref(), &topics, &req.packs);
        // The intent head reads the standalone question. Its best guess sets the cheap choices; only a
        // confident "calc" may start the slow LLM compute call.
        let emb = self.enc.as_ref().map(|e| e.encode(&route.query)).transpose()?;
        let intent = emb.as_deref().map_or("lookup", |e| Head::intent().best(e));
        let calc_sure = emb.as_deref().is_some_and(|e| Head::intent().classify(e) == "calc");
        // The rewrite model also marks comparisons, with the conversation in view ("is K2 much shorter?").
        route.compare |= intent == "compare" || rewritten.as_ref().is_some_and(|rw| rw.compare);
        route.complex |= route.compare || intent != "lookup";
        rec.intent = intent.to_string();
        let raw_query = route.query.clone();
        // Follow-ups without the rewrite step. A message that the follow-up head reads as needing the last turn
        // takes its topic, and its entities when the message names at most one thing; so does a comparison that
        // names one thing ("Is France bigger?"). An expand request searches the last question again.
        let prev = req.history.last().filter(|_| rewritten.is_none());
        let expand = label == "expand" && prev.is_some();
        let compares = !expand && emb.as_deref().is_some_and(|e| Head::intent().prob(e, "compare") >= COMPARE_PROB);
        if (route.compare || compares) && route.entities.len() < 2 {
            // A comparison of common nouns ("Compare lions and tigers") names its things in lowercase.
            let things = route::compared_things(&lib, &route.query, self.rr.as_ref(), &req.packs);
            if things.len() >= 2 {
                route.entities = things;
                route.entities.truncate(3);
            }
        }
        let own = route.entities.len();
        let follow = emb.as_deref().map_or(0.0, |e| Head::follow_up().prob(e, "followup"));
        let about_one = |p: &Turn| route::route(&lib, &p.query, self.rr.as_ref(), &[], &req.packs).entities.len() == 1;
        route.needs_rewrite = prev.is_some_and(|p| expand || follow >= 0.5 || (own == 0 && follow >= FOLLOW_UP_LOW && about_one(p)));
        let inherit = expand || (own == 0 && route.needs_rewrite) || (prev.is_some() && own == 1 && (route.needs_rewrite || compares));
        let mut focus: Option<String> = None;
        let mut referent = req.history.len().saturating_sub(1);
        if (inherit || route.needs_rewrite) && prev.is_some() {
            let at = self.referent(&req.history, &raw_query, expand);
            referent = at;
            let prev = &req.history[at];
            // The topic is what the last question names. A last question that named nothing and followed up
            // itself ("What did she discover?" after "Who was Marie Curie?") passes on the one before.
            let mut prev_entities = vec![];
            let mut topic = prev;
            for t in req.history[..=at].iter().rev().take(2) {
                topic = t;
                prev_entities = route::route(&lib, &t.query, self.rr.as_ref(), &[], &req.packs).entities;
                let follows = |enc: &QueryEncoder| -> Result<bool> { Ok(Head::follow_up().classify(&enc.encode(&t.query)?) == "followup") };
                if !prev_entities.is_empty() || !self.enc.as_ref().map(follows).transpose()?.unwrap_or(false) {
                    break;
                }
            }
            let mut from_answer = prev_entities.is_empty();
            if from_answer {
                // "Who invented the telephone?" has no name in it, but the first sentence of its answer does.
                let plain = citations::plain_text(&citations::check(&prev.answer, 0, &HashSet::new()));
                let first = crate::text::sentences(&plain).into_iter().next().unwrap_or_default();
                prev_entities = route::link_entities(&lib, first, &req.packs);
                prev_entities.sort_by_key(|e| first.find(&e.surface));
                prev_entities.truncate(2);
            }
            // The last question's own words steer better than names picked from its answer ("What is a leopard?"
            // answered "…genus Panthera, found in Africa…").
            let names: Vec<String> = if from_answer { vec![] } else { prev_entities.iter().map(|e| e.article.title.clone()).collect() };
            // A bare follow-up that names one new thing of the old one's kind ("What about Venus?" after "How hot is
            // Mars?") asks the last question about it alone: the new name replaces the old one there.
            let swap = match (prev_entities.as_slice(), route.entities.as_slice()) {
                ([old], [new])
                    if !expand
                        && !from_answer
                        && !(route.compare || compares)
                        && (old.pack, old.article.id) != (new.pack, new.article.id)
                        && topic.query.contains(&old.surface)
                        && route.query.replacen(&new.surface, "", 1).split_whitespace().filter(|w| w.chars().any(char::is_alphanumeric)).count() <= 2
                        && route::same_kind(&lib, &req.packs, &old.article, &new.article) =>
                {
                    Some(topic.query.replacen(&old.surface, &new.surface, 1))
                }
                _ => None,
            };
            // The first sentence of the referred answer usually states its subject ("The longest river in Europe is
            // the Volga."), where its question's names can be a list or another sense. It needs an answer from this
            // engine (with sources); a comparison keeps the names it compared, and a message that names the referred
            // question's thing ("Tell me more about Everest") keeps that name.
            let renamed = route.entities.iter().any(|e| prev_entities.iter().any(|p| (p.pack, p.article.id) == (e.pack, e.article.id)));
            let said = if expand || swap.is_some() || route.compare || compares || renamed || (!from_answer && prev_entities.len() >= 2) || prev.sources.is_empty() {
                None
            } else {
                let plain = citations::plain_text(&citations::check(&prev.answer, 0, &HashSet::new()));
                let first = crate::text::sentences(&plain).into_iter().next().unwrap_or_default().trim().to_string();
                let mut ents = route::link_entities(&lib, &first, &req.packs);
                ents.sort_by_key(|e| first.find(&e.surface));
                ents.truncate(2);
                (!ents.is_empty()).then_some((first, ents))
            };
            if expand {
                route.query = format!("{} {}", names.join(" "), prev.query).trim_start().to_string();
                route.entities.clear();
                route.needs_rewrite = false;
                route.compare = false;
            } else if let Some(q) = &swap {
                route.query = q.clone();
            } else if let Some((first, ents)) = said {
                // The search reads the whole sentence; the evidence and the answer read the earlier question and this one.
                focus = Some(format!("{} {}", prev.query.trim(), route.query));
                route.query = format!("{first} {}", route.query);
                prev_entities = ents;
                from_answer = true;
            } else if !names.is_empty() {
                route.query = format!("{} {}", names.join(" "), route.query);
            } else {
                route.query = format!("{} {}", prev.query, route.query);
            }
            // Names read from an answer steer the search, but take no slot among compared things: "What is a
            // crocodile?" answered "…in Africa, Asia…" compares no continent.
            if inherit && swap.is_none() && !(from_answer && (route.compare || compares)) {
                route.entities.extend(prev_entities);
                route.entities.truncate(3);
            }
        }
        route.compare |= compares && route.entities.len() >= 2;
        route.complex |= route.compare;
        rec.complex = route.complex;
        rec.search_query = route.query.clone();
        let retriever = Retriever { lib: &lib, enc: self.enc.as_ref(), rr: self.rr.as_ref(), s: &s.retrieval, scope: &req.packs };
        let ranked = retriever.ranked(&route.query, &route.entities, &mut rec.retrieval)?;
        // At most three passages per article, so one long article cannot crowd out the others. "Tell me more"
        // wants new facts: the passages the last turn showed only fill slots that no other passage can.
        let shown: HashSet<(&str, u32)> = prev.filter(|_| expand).map_or_else(HashSet::new, |p| p.sources.iter().map(|r| (r.pack_id.as_str(), r.passage_id)).collect());
        let pick = |ranked: &[Hit]| {
            let (old, fresh): (Vec<Hit>, Vec<Hit>) = ranked.iter().cloned().partition(|h| shown.contains(&(lib.packs[h.pack as usize].manifest.pack_id.as_str(), h.pid)));
            let mut hits = select_diverse(&fresh, s.retrieval.rerank_keep, 3, &[]);
            hits.extend(select_diverse(&old, s.retrieval.rerank_keep - hits.len(), 3, &[]));
            hits
        };
        let mut hits = pick(&ranked);
        let scores = emb.as_deref().map(prop_scores).unwrap_or_default();
        let (card_facts, mut wd) = Self::wikidata_facts(&lib, &route, hits.first(), &scores, route.compare || intent == "calc", 6, &req.packs);
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
                facts: card_facts,
                computed: unit_line.iter().cloned().collect(),
                sources: hits.iter().map(|h| Self::source_ref(&lib, h, 0)).collect(),
                entities: route.entities.iter().map(|e| e.article.title.clone()).collect(),
                card_ms: ms(t0),
                featured: None,
            }
        };
        rec.card_ms = card.card_ms;
        sink.event(Event::Card(card.clone()));

        // Stage 2b: featured snippet, the reader's answer span in the top passages.
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
            // Agreement: the span must appear in two passages or in a Wikidata fact.
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

        // After the card: each chain the hop reranks costs ~18 ms on the Pixel.
        if let Some(rr) = self.rr.as_ref() {
            let chain = Self::wikidata_hop(&lib, rr, &route, hits.first(), &scores, &req.packs)?;
            if let Some((bridge, asked)) = chain.split_first() {
                if !wd.iter().any(|f| f.qid == bridge.qid && f.pid == bridge.pid) {
                    let at = wd.iter().rposition(|f| f.qid == bridge.qid).map_or(wd.len(), |i| i + 1);
                    wd.insert(at, bridge.clone());
                }
                wd.extend(asked.iter().cloned());
            }
        }

        // A deep answer has room for context beyond the best passages; a short one would lose a source to it.
        let ranked = if req.deep {
            let ranked = retriever.expand(&route.query, ranked, &mut rec.retrieval)?;
            hits = pick(&ranked);
            ranked
        } else {
            ranked
        };

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
        // Comparisons get one template search per entity instead.
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
            // The first search's pool keeps its rerank scores while the question is the same.
            let mut pool: Vec<Hit> = if route.needs_rewrite { Vec::new() } else { ranked.clone() };
            if question != route.query {
                pool.iter_mut().for_each(|h| h.rerank = None);
            }
            let mut queries = plan.subqueries.clone();
            if route.needs_rewrite || queries.is_empty() {
                queries.push(question.clone());
            }
            for q in &queries {
                let ents = route::link_entities(&lib, q, &req.packs);
                let mut st = Default::default();
                for h in retriever.fused(q, &ents, &mut st)? {
                    if !pool.iter().any(|p| p.pack == h.pack && p.pid == h.pid) {
                        pool.push(h);
                    }
                }
            }
            pool.sort_by(|a, b| b.fused.total_cmp(&a.fused));
            pool.truncate(s.retrieval.fuse_keep * 2);
            let mut reranked = retriever.rerank(&question, pool, 24, &mut rec.retrieval)?;
            let must: Vec<(u8, u32)> = if route.compare { route.entities.iter().map(|e| (e.pack, e.article.id)).collect() } else { vec![] };
            if route.compare {
                // A compared thing that no passage in the pool covers still gets its article's lead.
                for e in &route.entities {
                    if !reranked.iter().any(|h| (h.pack, h.article.id) == (e.pack, e.article.id)) {
                        reranked.extend(retriever.load(&[((e.pack, e.article.first_passage), 0.0)])?);
                    }
                }
            }
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
        // A follow-up searched with an earlier answer's sentence: the evidence windows and the answer read the
        // earlier question and this one instead, so that sentence does not pull them back to the old answer.
        let question = match &focus {
            Some(f) if question == route.query => f.clone(),
            _ => question,
        };
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
        // The user's own numbers ("walking 5 km an hour") are known, to the compute check and the answer check.
        ev.numbers.extend(crate::text::numbers(&req.query));
        ev.numbers.extend(crate::text::numbers(&question));
        stage(&mut rec, Stage::Reading, ev.count().to_string());

        // Stage 3b: compute. Wikidata values first (no LLM); the LLM compute call re-reads the whole evidence
        // (10–13 s on the Pixel), so it runs only for an explicit calculation that Wikidata cannot answer.
        let wants_numbers = plan.needs_numbers || plan.intent == "calc" || intent == "calc";
        // Only a question that asks for a number gets one computed: "Compare Mars and Venus" would get the ratio of
        // whichever number matches best, and the answer would lead with it.
        let asks_number = |e: &QueryEncoder| -> Result<bool> {
            let masked = prop_scores(&e.encode(&without_names(&route))?);
            Ok(masked.iter().any(|s| s.quantity && s.score >= NUMBER_ASKED))
        };
        let calcs = match &self.enc {
            Some(e) if (wants_numbers || route.compare || route.entities.len() >= 2) && !route.entities.is_empty() && asks_number(e)? => {
                Self::wikidata_calcs(&lib, &route, &scores, &req.packs)
            }
            _ => vec![],
        };
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
                    // One-digit integers are constants ("/ 2", "4 / 3"), not data the model could make up.
                    let known = |l: &String| {
                        let short = l.trim_end_matches(".0");
                        ev.numbers.contains(short) || ev.numbers.contains(l) || (short.len() == 1 && short.as_bytes()[0].is_ascii_digit())
                    };
                    if lits.is_empty() || !lits.iter().all(known) {
                        continue;
                    }
                    if let Ok(v) = calc::eval(&calc.expr) {
                        ev.add_computed(format!("{} = {}", calc.label.trim(), format_num(v)));
                    }
                }
            }
        }

        let mut sources: Vec<SourceRef> = ev.sources.iter().map(|src| Self::source_ref(&lib, &src.hit, src.n)).collect();
        sources.extend(ev.wikidata.iter().map(|w| Self::wikidata_ref(&lib, w)));
        sink.event(Event::Sources(sources.clone()));
        rec.n_sources = sources.len();

        // Stage 4: synthesis with the cached system prefix (after the reasoning, in thinking mode).
        stage(&mut rec, if req.think { Stage::Thinking } else { Stage::Writing }, String::new());
        let rendered = ev.render(&lib);
        rec.evidence = rendered.clone();
        rec.evidence_tokens = crate::text::approx_tokens(&rendered);
        ev.numbers.extend(crate::text::numbers(&lib.snapshot_date()));
        let system = prompts::synthesis_system(&lib.snapshot_date(), !req.deep);
        // Keep the user's own words in front of the model; a rewrite from a small planner can drift.
        let asked = if raw_query != question && !req.history.is_empty() { format!("{} ({question})", req.query.trim()) } else { question.clone() };
        // The exchange the follow-up refers to, so it builds on it. Its citations point at another turn's sources.
        let earlier = req.history.get(referent).map(|t| {
            let plain = citations::plain_text(&citations::check(&t.answer, 0, &HashSet::new()));
            (t.query.as_str(), trunc(&crate::text::sentences(&plain).into_iter().take(2).collect::<Vec<_>>().join(" "), 300))
        });
        let user = prompts::synthesis_user(&rendered, &asked, earlier.as_ref().map(|(q, a)| (*q, a.as_str())));
        let n = ev.count();
        let max = if req.deep { s.deep_max_answer_tokens } else { s.max_answer_tokens };
        let mut params = GenParams::synthesis(max);
        params.think_budget = req.think.then_some(s.think_budget);
        params.keep_prefix = true;
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

/// The question without the names of its entities: "which was bigger, the or the ?".
fn without_names(route: &Route) -> String {
    let mut q = route.query.to_lowercase();
    for e in &route.entities {
        q = q.replace(&e.surface.to_lowercase(), " ");
    }
    q.split_whitespace().collect::<Vec<_>>().join(" ")
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
