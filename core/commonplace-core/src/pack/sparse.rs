//! tantivy BM25 index over passages. Fields: title, section, body, questions (doc2query), pid (fast).
//! Positions are off to keep the index small, so the query side strips phrase syntax.

use anyhow::Result;
use std::path::Path;
use tantivy::collector::TopDocs;
use tantivy::columnar::Column;
use tantivy::query::QueryParser;
use tantivy::schema::{FAST, Field, IndexRecordOption, Schema, TextFieldIndexing, TextOptions};
use tantivy::tokenizer::{Language, LowerCaser, RemoveLongFilter, SimpleTokenizer, Stemmer, StopWordFilter, TextAnalyzer};
use tantivy::{Index, IndexReader, IndexWriter, ReloadPolicy, TantivyDocument};

pub const TANTIVY_VERSION: &str = "0.26.2";
const ANALYZER: &str = "cp_en";

pub struct Fields {
    pub title: Field,
    pub section: Field,
    pub body: Field,
    pub questions: Field,
    pub pid: Field,
}

pub fn schema() -> (Schema, Fields) {
    let mut b = Schema::builder();
    let text = TextOptions::default()
        .set_indexing_options(TextFieldIndexing::default().set_tokenizer(ANALYZER).set_index_option(IndexRecordOption::WithFreqs));
    let f = Fields {
        title: b.add_text_field("title", text.clone()),
        section: b.add_text_field("section", text.clone()),
        body: b.add_text_field("body", text.clone()),
        questions: b.add_text_field("questions", text),
        pid: b.add_u64_field("pid", FAST),
    };
    (b.build(), f)
}

fn register(index: &Index) {
    let analyzer = TextAnalyzer::builder(SimpleTokenizer::default())
        .filter(RemoveLongFilter::limit(40))
        .filter(LowerCaser)
        .filter(StopWordFilter::new(Language::English).unwrap())
        .filter(Stemmer::new(Language::English))
        .build();
    index.tokenizers().register(ANALYZER, analyzer);
}

pub struct Boosts {
    pub title: f32,
    pub section: f32,
    pub body: f32,
    pub questions: f32,
}

impl Default for Boosts {
    fn default() -> Self {
        Self { title: 3.0, section: 1.5, body: 1.0, questions: 2.0 }
    }
}

pub struct SparseIndex {
    reader: IndexReader,
    parser: QueryParser,
    pids: Vec<Column<u64>>,
}

impl SparseIndex {
    pub fn open(dir: &Path) -> Result<Self> {
        let index = Index::open_in_dir(dir)?;
        register(&index);
        let schema = index.schema();
        let get = |n: &str| schema.get_field(n);
        let (title, section, body, questions) = (get("title")?, get("section")?, get("body")?, get("questions")?);
        let mut parser = QueryParser::for_index(&index, vec![title, section, body, questions]);
        let b = Boosts::default();
        parser.set_field_boost(title, b.title);
        parser.set_field_boost(section, b.section);
        parser.set_field_boost(body, b.body);
        parser.set_field_boost(questions, b.questions);
        let reader = index.reader_builder().reload_policy(ReloadPolicy::Manual).try_into()?;
        let searcher = reader.searcher();
        let pids = searcher.segment_readers().iter().map(|s| s.fast_fields().u64("pid")).collect::<tantivy::Result<_>>()?;
        Ok(Self { reader, parser, pids })
    }

    /// BM25 search. Returns (passage_id, score), best first.
    pub fn search(&self, query: &str, k: usize) -> Result<Vec<(u32, f32)>> {
        let cleaned: String =
            query.chars().map(|c| if c.is_alphanumeric() || c == '\'' || c == '-' { c } else { ' ' }).collect();
        let cleaned = cleaned.split_whitespace().filter(|w| !matches!(*w, "AND" | "OR" | "NOT")).collect::<Vec<_>>().join(" ");
        if cleaned.is_empty() {
            return Ok(Vec::new());
        }
        let (q, _errors) = self.parser.parse_query_lenient(&cleaned);
        let searcher = self.reader.searcher();
        let hits = searcher.search(&q, &TopDocs::with_limit(k).order_by_score())?;
        Ok(hits
            .into_iter()
            .filter_map(|(score, addr)| {
                let pid = self.pids[addr.segment_ord as usize].first(addr.doc_id)?;
                Some((pid as u32, score))
            })
            .collect())
    }
}

pub struct SparseWriter {
    index: Index,
    writer: IndexWriter,
    f: Fields,
}

impl SparseWriter {
    pub fn create(dir: &Path, heap_bytes: usize) -> Result<Self> {
        std::fs::create_dir_all(dir)?;
        let (schema, f) = schema();
        let index = Index::create_in_dir(dir, schema)?;
        register(&index);
        let writer: IndexWriter = index.writer(heap_bytes)?;
        // Merge once at the end; background merges race with the final merge.
        writer.set_merge_policy(Box::new(tantivy::indexer::NoMergePolicy));
        Ok(Self { index, writer, f })
    }

    pub fn add(&mut self, pid: u32, title: &str, section: &str, body: &str, questions: &str) -> Result<()> {
        let mut doc = TantivyDocument::default();
        doc.add_u64(self.f.pid, pid as u64);
        doc.add_text(self.f.title, title);
        doc.add_text(self.f.section, section);
        doc.add_text(self.f.body, body);
        if !questions.is_empty() {
            doc.add_text(self.f.questions, questions);
        }
        self.writer.add_document(doc)?;
        Ok(())
    }

    pub fn finish(mut self) -> Result<()> {
        self.writer.commit()?;
        let ids = self.index.searchable_segment_ids()?;
        if ids.len() > 1 {
            self.writer.merge(&ids).wait()?;
        }
        self.writer.wait_merging_threads()?;
        Ok(())
    }
}
