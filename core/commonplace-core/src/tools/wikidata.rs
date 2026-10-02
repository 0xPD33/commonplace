//! Structured facts from the `wikidata-facts` pack (`wikidata.sqlite`).

use anyhow::Result;
use rusqlite::{Connection, OpenFlags, params};
use serde::Serialize;
use std::path::Path;
use std::sync::Mutex;

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
    /// Evidence line, e.g. `Malta — population: 563,443 (2024)`.
    pub fn line(&self) -> String {
        match &self.point_in_time {
            Some(t) => format!("{} — {}: {} ({})", self.entity, self.label, self.value, t),
            None => format!("{} — {}: {}", self.entity, self.label, self.value),
        }
    }
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
