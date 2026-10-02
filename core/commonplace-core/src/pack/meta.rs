//! `meta.sqlite`: articles and redirects. Passage metadata lives in the frame store.

use anyhow::Result;
use rusqlite::{Connection, OpenFlags, OptionalExtension, params};
use std::path::Path;
use std::sync::Mutex;

pub const SCHEMA: &str = "
CREATE TABLE articles(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, title_norm TEXT NOT NULL, qid TEXT,
  popularity INTEGER NOT NULL DEFAULT 0, url_title TEXT, oneliner TEXT,
  first_passage INTEGER NOT NULL, n_passages INTEGER NOT NULL);
CREATE TABLE redirects(from_norm TEXT PRIMARY KEY, article_id INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE sources(name TEXT, url TEXT, license TEXT, snapshot TEXT);
";

pub const INDEXES: &str = "
CREATE INDEX articles_title_norm ON articles(title_norm);
CREATE INDEX articles_qid ON articles(qid);
";

#[derive(Debug, Clone)]
pub struct Article {
    pub id: u32,
    pub title: String,
    pub qid: Option<String>,
    pub popularity: u64,
    pub url_title: Option<String>,
    pub oneliner: Option<String>,
    pub first_passage: u32,
    pub n_passages: u32,
}

pub struct MetaDb {
    conn: Mutex<Connection>,
}

const ARTICLE_COLS: &str = "id, title, qid, popularity, url_title, oneliner, first_passage, n_passages";

fn row_article(r: &rusqlite::Row) -> rusqlite::Result<Article> {
    Ok(Article {
        id: r.get(0)?,
        title: r.get(1)?,
        qid: r.get(2)?,
        popularity: r.get::<_, i64>(3)? as u64,
        url_title: r.get(4)?,
        oneliner: r.get(5)?,
        first_passage: r.get(6)?,
        n_passages: r.get(7)?,
    })
}

impl MetaDb {
    pub fn open(path: &Path) -> Result<Self> {
        let uri = format!("file:{}?immutable=1", path.display());
        let conn = Connection::open_with_flags(uri, OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI)?;
        Ok(Self { conn: Mutex::new(conn) })
    }

    pub fn article(&self, id: u32) -> Result<Option<Article>> {
        let c = self.conn.lock().unwrap();
        Ok(c.query_row(&format!("SELECT {ARTICLE_COLS} FROM articles WHERE id = ?"), [id], row_article).optional()?)
    }

    pub fn articles(&self, ids: &[u32]) -> Result<Vec<Article>> {
        let c = self.conn.lock().unwrap();
        let mut st = c.prepare_cached(&format!("SELECT {ARTICLE_COLS} FROM articles WHERE id = ?"))?;
        let mut out = Vec::with_capacity(ids.len());
        for id in ids {
            if let Some(a) = st.query_row([id], row_article).optional()? {
                out.push(a);
            }
        }
        Ok(out)
    }

    /// Resolve a normalized title (or redirect) to the most popular matching article.
    pub fn lookup_title(&self, norm: &str) -> Result<Option<Article>> {
        let c = self.conn.lock().unwrap();
        let direct = c
            .prepare_cached(&format!("SELECT {ARTICLE_COLS} FROM articles WHERE title_norm = ? ORDER BY popularity DESC LIMIT 1"))?
            .query_row([norm], row_article)
            .optional()?;
        if direct.is_some() {
            return Ok(direct);
        }
        Ok(c.prepare_cached(&format!(
            "SELECT {} FROM redirects r JOIN articles a ON a.id = r.article_id WHERE r.from_norm = ?",
            ARTICLE_COLS.split(", ").map(|c| format!("a.{c}")).collect::<Vec<_>>().join(", ")
        ))?
        .query_row([norm], row_article)
        .optional()?)
    }

    /// Articles a surface form may mean: the exact title, "X (…)" and "X …" titles ("Amazon River"),
    /// most popular first. Used to disambiguate with the question as context.
    pub fn candidates(&self, norm: &str, limit: usize) -> Result<Vec<Article>> {
        let c = self.conn.lock().unwrap();
        let mut st = c.prepare_cached(&format!(
            "SELECT {ARTICLE_COLS} FROM articles WHERE title_norm = ?1 OR (title_norm > ?1 || ' ' AND title_norm < ?1 || ' ~')
             ORDER BY popularity DESC LIMIT ?2"
        ))?;
        let rows = st.query_map(rusqlite::params![norm, limit as i64], row_article)?;
        Ok(rows.collect::<rusqlite::Result<Vec<_>>>()?)
    }

    /// Resolve an all-caps code ("US", "UK") through its case-sensitive redirect key.
    pub fn lookup_code(&self, code: &str) -> Result<Option<Article>> {
        let c = self.conn.lock().unwrap();
        Ok(c.prepare_cached(&format!(
            "SELECT {} FROM redirects r JOIN articles a ON a.id = r.article_id WHERE r.from_norm = ?",
            ARTICLE_COLS.split(", ").map(|c| format!("a.{c}")).collect::<Vec<_>>().join(", ")
        ))?
        .query_row([code], row_article)
        .optional()?)
    }

    pub fn article_count(&self) -> Result<u64> {
        let c = self.conn.lock().unwrap();
        Ok(c.query_row("SELECT COUNT(*) FROM articles", [], |r| r.get::<_, i64>(0))? as u64)
    }
}

/// Writer used by packbuild.
pub struct MetaWriter {
    conn: Connection,
}

impl MetaWriter {
    pub fn create(path: &Path) -> Result<Self> {
        let _ = std::fs::remove_file(path);
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA page_size=4096;")?;
        conn.execute_batch(SCHEMA)?;
        conn.execute_batch("BEGIN")?;
        Ok(Self { conn })
    }

    /// `linkable == false` (disambiguation pages) keeps the article searchable but out of title lookup.
    pub fn add_article(&mut self, a: &Article, linkable: bool) -> Result<()> {
        self.conn
            .prepare_cached("INSERT INTO articles VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)")?
            .execute(params![
                a.id,
                a.title,
                if linkable { super::normalize_title(&a.title) } else { String::new() },
                a.qid,
                a.popularity as i64,
                a.url_title,
                a.oneliner,
                a.first_passage,
                a.n_passages
            ])?;
        Ok(())
    }

    pub fn add_redirect(&mut self, from: &str, article_id: u32) -> Result<()> {
        self.conn
            .prepare_cached("INSERT OR IGNORE INTO redirects VALUES (?1, ?2)")?
            .execute(params![super::normalize_title(from), article_id])?;
        Ok(())
    }

    pub fn add_source(&mut self, name: &str, url: &str, license: &str, snapshot: &str) -> Result<()> {
        self.conn.execute("INSERT INTO sources VALUES (?1, ?2, ?3, ?4)", params![name, url, license, snapshot])?;
        Ok(())
    }

    pub fn finish(self) -> Result<()> {
        self.conn.execute_batch("COMMIT")?;
        self.conn.execute_batch(INDEXES)?;
        self.conn.execute_batch("ANALYZE; VACUUM;")?;
        Ok(())
    }
}
