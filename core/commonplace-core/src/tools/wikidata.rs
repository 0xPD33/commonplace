//! Structured facts from the `wikidata-facts` pack (`wikidata.sqlite`).

use anyhow::Result;
use rusqlite::{Connection, OpenFlags, params};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::{LazyLock, Mutex};

pub const SCHEMA: &str = "
CREATE TABLE props(pid TEXT PRIMARY KEY, label TEXT NOT NULL, priority INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE entities(qid TEXT PRIMARY KEY, label TEXT NOT NULL, aliases TEXT) WITHOUT ROWID;
CREATE TABLE facts(qid TEXT NOT NULL, pid TEXT NOT NULL, value_num REAL, value_text TEXT, unit TEXT,
  point_in_time TEXT, rank INTEGER NOT NULL DEFAULT 1);
";
pub const INDEXES: &str = "CREATE INDEX facts_qid ON facts(qid, pid);";

#[derive(Debug, Clone, Serialize)]
pub struct WdFact {
    pub qid: String,
    pub entity: String,
    pub pid: String,
    pub label: String,
    pub value: String,
    pub point_in_time: Option<String>,
    /// Numeric value, for tools and the evidence number check.
    pub value_num: Option<f64>,
}

impl WdFact {
    /// Line with the entity name, e.g. `Malta — population: 563,443 (2024)`.
    pub fn line(&self) -> String {
        match &self.point_in_time {
            Some(t) => format!("{} — {}: {} ({})", self.entity, self.label, self.value, t),
            None => format!("{} — {}: {}", self.entity, self.label, self.value),
        }
    }

    /// Line under the entity's heading, e.g. `population: 563,443 (2024)`.
    pub fn short_line(&self) -> String {
        match &self.point_in_time {
            Some(t) => format!("{}: {} ({t})", self.label, self.value),
            None => format!("{}: {}", self.label, self.value),
        }
    }

    /// The QID as a number (`Q30` → 30): the passage and article id of a Wikidata source.
    pub fn qid_num(&self) -> u32 {
        self.qid.trim_start_matches('Q').parse().unwrap_or(0)
    }
}

#[derive(Deserialize)]
struct PropFile {
    dims: usize,
    props: Vec<PropEntry>,
}

#[derive(Deserialize)]
struct PropEntry {
    id: String,
    label: String,
    /// Its values are numbers.
    #[serde(default)]
    quantity: bool,
    scale: f32,
    /// int8 values as hex.
    v: String,
}

/// A property, or a ratio of two ("P1082/P2046"), with its cosine to the question minus the mean cosine of all
/// properties: how much more than the average property the question asks about it.
#[derive(Debug, Clone)]
pub struct PropScore {
    pub id: &'static str,
    pub label: &'static str,
    pub quantity: bool,
    pub score: f32,
}

struct PropVector {
    id: String,
    label: String,
    quantity: bool,
    v: Vec<f32>,
}

/// Unit vectors written by `pipeline/commonplace_pipeline/prop_vectors.py`, in the query encoder's space, and their size.
static PROP_VECTORS: LazyLock<(Vec<PropVector>, usize)> = LazyLock::new(|| {
    let f: PropFile = serde_json::from_str(include_str!("../wikidata_props.json")).expect("wikidata_props.json");
    let props = f
        .props
        .into_iter()
        .map(|p| {
            let v: Vec<f32> = hex::decode(&p.v).expect("wikidata_props.json hex").into_iter().map(|b| b as i8 as f32 * p.scale).collect();
            let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt().max(1e-12);
            PropVector { id: p.id, label: p.label, quantity: p.quantity, v: v.into_iter().map(|x| x / norm).collect() }
        })
        .collect();
    (props, f.dims)
});

/// Every property's score for a question embedding; empty when the embedding has other dimensions.
pub fn prop_scores(emb: &[f32]) -> Vec<PropScore> {
    let (props, dims) = &*PROP_VECTORS;
    if emb.len() != *dims || props.is_empty() {
        return vec![];
    }
    let cos: Vec<f32> = props.iter().map(|p| p.v.iter().zip(emb).map(|(a, b)| a * b).sum()).collect();
    let mean = cos.iter().sum::<f32>() / cos.len() as f32;
    props.iter().zip(cos).map(|(p, c)| PropScore { id: &p.id, label: &p.label, quantity: p.quantity, score: c - mean }).collect()
}

pub fn format_num(x: f64) -> String {
    if x.fract() == 0.0 && x.abs() < 1e15 {
        let s = format!("{}", x as i64);
        let (sign, digits) = s.strip_prefix('-').map(|d| ("-", d)).unwrap_or(("", &s));
        let mut out = String::new();
        for (i, c) in digits.chars().enumerate() {
            if i > 0 && (digits.len() - i) % 3 == 0 {
                out.push(',');
            }
            out.push(c);
        }
        format!("{sign}{out}")
    } else {
        let s = format!("{x:.4}");
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    }
}

pub struct WikidataDb {
    conn: Mutex<Connection>,
}

impl WikidataDb {
    pub fn open(path: &Path) -> Result<Self> {
        let uri = format!("file:{}?immutable=1", path.display());
        let conn = Connection::open_with_flags(uri, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI)?;
        Ok(Self { conn: Mutex::new(conn) })
    }

    /// Key facts for an entity: best-ranked, latest statement per property, highest priority first.
    pub fn facts(&self, qid: &str, limit: usize) -> Result<Vec<WdFact>> {
        let c = self.conn.lock().unwrap();
        let entity: String = c
            .query_row("SELECT label FROM entities WHERE qid = ?", [qid], |r| r.get(0))
            .unwrap_or_else(|_| qid.to_string());
        let mut st = c.prepare_cached(
            "SELECT f.pid, p.label, f.value_num, f.value_text, f.unit, f.point_in_time
             FROM facts f JOIN props p ON p.pid = f.pid
             WHERE f.qid = ?1
             ORDER BY p.priority, f.pid, f.rank DESC, f.point_in_time DESC",
        )?;
        let rows = st.query_map(params![qid], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<f64>>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, Option<String>>(4)?,
                r.get::<_, Option<String>>(5)?,
            ))
        })?;
        let mut out: Vec<WdFact> = Vec::new();
        for row in rows {
            let (pid, label, num, text, unit, when) = row?;
            if out.iter().any(|f| f.pid == pid) {
                continue;
            }
            let value = match (num, text) {
                (Some(n), _) => match unit.as_deref() {
                    Some(u) if !u.is_empty() => format!("{} {u}", format_num(n)),
                    _ => format_num(n),
                },
                (None, Some(t)) => t,
                (None, None) => continue,
            };
            out.push(WdFact { qid: qid.to_string(), entity: entity.clone(), pid, label, value, point_in_time: when, value_num: num });
            if out.len() >= limit {
                break;
            }
        }
        Ok(out)
    }
}
