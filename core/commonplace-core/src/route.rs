//! Stage 0: normalize, detect follow-ups, link entities. No LLM. The engine adds the question's intent
//! (`encoders::Head::intent`).

use crate::library::Library;
use crate::pack::{Pack, meta::Article, normalize_title};
use crate::encoders::Reranker;
use crate::text::STOPWORDS;

#[derive(Debug, Clone)]
pub struct Entity {
    pub pack: u8,
    pub article: Article,
    pub surface: String,
}

#[derive(Debug, Clone)]
pub struct Route {
    pub query: String,
    pub complex: bool,
    pub needs_rewrite: bool,
    pub compare: bool,
    pub entities: Vec<Entity>,
}

/// Comparison wording, removed from a comparison's attribute: it names no property to search for.
const COMPARE_WORDS: &[&str] = &[" vs ", " vs. ", " versus ", "compare", "difference between", "differences between", "better than", "worse than", "similarities"];
const FOLLOWUP_WORDS: &[&str] = &[
    "it", "its", "they", "them", "their", "there", "he", "she", "his", "her", "this", "that", "these", "those", "one", "ones", "both", "either",
    "neither", "former", "latter",
];
const FOLLOWUP_STARTS: &[&str] = &["what about", "how about", "and ", "also ", "what else", "why not", "tell me more"];
/// Question-opening verbs: capitalized only because they start the sentence ("Compare the Nile…").
const LEADING_VERBS: &[&str] = &["compare", "explain", "describe", "define", "list", "name", "give", "show", "find", "summarize", "summarise"];

pub fn normalize(q: &str) -> String {
    q.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `rr` lets entity linking pick among same-name articles by the question's context. `topics` are the
/// article titles the rewrite model named; when any of them exists, they are the entities.
/// `scope` limits entity linking to those pack ids (empty: every pack).
pub fn route(lib: &Library, query: &str, has_history: bool, rr: Option<&Reranker>, topics: &[String], scope: &[String]) -> Route {
    let query = normalize(query);
    let lower = format!(" {} ", query.to_lowercase());
    let words: Vec<&str> = query.split_whitespace().collect();
    let needs_rewrite = has_history
        && (FOLLOWUP_STARTS.iter().any(|s| lower.trim_start().starts_with(s))
            || words.iter().any(|w| FOLLOWUP_WORDS.contains(&w.to_lowercase().trim_matches(|c: char| !c.is_alphanumeric())))
            || words.len() <= 3);
    let mut entities: Vec<Entity> = Vec::new();
    for t in topics {
        // A title the model named must exist as a title or a redirect; no guessing from its words.
        let norm = normalize_title(t);
        let found = lib
            .scoped(scope)
            .filter_map(|(pi, p)| p.meta.lookup_title(&norm).ok().flatten().map(|a| (pi, a)))
            .max_by_key(|(_, a)| a.popularity);
        if let Some((pack, article)) = found.filter(|(p, a)| !entities.iter().any(|x: &Entity| x.pack == *p && x.article.id == a.id)) {
            entities.push(Entity { pack, article, surface: t.clone() });
        }
    }
    if entities.is_empty() {
        entities = link_entities_with(lib, &query, rr, scope);
    }
    let complex = entities.len() >= 2 || words.len() > 14;
    Route { query, complex, needs_rewrite, compare: false, entities }
}

/// Longest-first n-gram lookup against article titles and redirects in every pack.
/// Single words must be capitalized in the query (or the query must be very short) to limit noise.
pub fn link_entities(lib: &Library, query: &str, scope: &[String]) -> Vec<Entity> {
    link_entities_with(lib, query, None, scope)
}

/// Margin (reranker logits) a less popular same-name article needs to replace the most popular one.
const DISAMBIG_MARGIN: f32 = 1.0;

fn link_entities_with(lib: &Library, query: &str, rr: Option<&Reranker>, scope: &[String]) -> Vec<Entity> {
    let toks: Vec<&str> = query
        .split(|c: char| c.is_whitespace() || matches!(c, '?' | '!' | ',' | ';' | ':' | '"'))
        .filter(|t| !t.is_empty())
        .collect();
    let short = toks.len() <= 3;
    let mut used = vec![false; toks.len()];
    let mut found: Vec<Entity> = Vec::new();
    for n in (1..=5.min(toks.len())).rev() {
        for i in 0..=toks.len() - n {
            if used[i..i + n].iter().any(|&u| u) {
                continue;
            }
            let span = &toks[i..i + n];
            let first = span[0].trim_matches(|c: char| !c.is_alphanumeric());
            let last = span[n - 1].trim_matches(|c: char| !c.is_alphanumeric());
            if STOPWORDS.contains(first.to_lowercase().as_str()) || STOPWORDS.contains(last.to_lowercase().as_str()) {
                continue;
            }
            if n == 1 && !short && !first.chars().next().is_some_and(char::is_uppercase) {
                continue;
            }
            if n == 1 && i == 0 && LEADING_VERBS.contains(&first.to_lowercase().as_str()) {
                continue;
            }
            let surface = span.join(" ").trim_matches(|c: char| !c.is_alphanumeric()).to_string();
            let norm = normalize_title(&surface);
            // "US" means the country, not the film "Us": all-caps codes resolve by their exact key first.
            let code = n == 1 && (2..=5).contains(&surface.len()) && surface.chars().all(|c| c.is_ascii_uppercase());
            let lookup = |f: &dyn Fn(&Pack) -> Option<Article>| {
                lib.scoped(scope).filter_map(|(pi, p)| f(p).map(|a| (pi, a))).max_by_key(|(_, a)| a.popularity)
            };
            let by_title = || lookup(&|p| p.meta.lookup_title(&norm).ok().flatten());
            let best = if code { lookup(&|p| p.meta.lookup_code(&surface).ok().flatten()).or_else(by_title) } else { by_title() };
            // Lowercase phrases ("light bulb", "northern lights") are common nouns: they never resolve to a
            // qualified title like "Light Bulb (Abbott Elementary)". A capitalized name with no article of
            // its own ("Mercury") may still mean one of its "(…)" variants.
            let lowercase = !surface.chars().any(char::is_uppercase);
            let best = best.or_else(|| {
                let variant = format!("{norm} (");
                (!lowercase && !code)
                    .then(|| lookup(&|p| p.meta.candidates(&norm, 6).ok()?.into_iter().find(|a| normalize_title(&a.title).starts_with(&variant))))
                    .flatten()
            });
            let best = match (rr, best) {
                (Some(rr), Some(b)) if !code && n <= 3 => disambiguate(lib, rr, query, &norm, b, lowercase, scope),
                (_, b) => b.filter(|(_, a)| !(lowercase && qualified(a))),
            };
            // A lowercase phrase inside a sentence names a thing only if it matches a proper name
            // ("golden gate bridge"), not a common-noun article such as "Sky blue" or "Population density".
            let best = best.filter(|(_, a)| !(lowercase && n > 1 && !short && !proper_name(&a.title)));
            if let Some((pack, article)) = best {
                used[i..i + n].iter_mut().for_each(|u| *u = true);
                if !found.iter().any(|e| e.article.qid.is_some() && e.article.qid == article.qid) {
                    found.push(Entity { pack, article, surface });
                }
            }
        }
    }
    found.sort_by_key(|e| std::cmp::Reverse(e.article.popularity));
    found.truncate(3);
    found
}

/// What a comparison is about: the question minus entity names, comparison words and stopwords.
/// "Compare the population of Malta and Cyprus" → "population".
pub fn attribute(route: &Route) -> String {
    let mut rest = format!(" {} ", route.query.to_lowercase());
    for e in &route.entities {
        rest = rest.replace(&e.surface.to_lowercase(), " ");
    }
    for m in COMPARE_WORDS {
        rest = rest.replace(m, " ");
    }
    rest.split(|c: char| !c.is_alphanumeric())
        .filter(|w| !w.is_empty() && !STOPWORDS.contains(w) && !matches!(*w, "or" | "vs" | "versus" | "which" | "both" | "between"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Pick among Wikipedia articles sharing a name ("Mercury", "Mercury (planet)", "Mercury (automobile)").
/// Start from the most viewed one (the primary topic), leave out rarely viewed ones (disambiguation
/// stubs), and let the question's context override only by a clear reranker margin.
/// `lowercase` (a common noun) rules out qualified titles; then nothing may be left.
fn disambiguate(lib: &Library, rr: &Reranker, query: &str, norm: &str, best: (u8, Article), lowercase: bool, scope: &[String]) -> Option<(u8, Article)> {
    let mut cands = vec![best];
    for (pi, p) in lib.scoped(scope).filter(|(_, p)| is_wikipedia(&p.manifest.pack_id)) {
        for a in p.meta.candidates(norm, 6).unwrap_or_default() {
            if !cands.iter().any(|(cp, c)| *cp == pi && c.id == a.id) {
                cands.push((pi, a));
            }
        }
    }
    // Same name ("Bridge", "Bridge (music)") competes on views; longer titles ("Bridge to Terabithia",
    // "Amazon River") are other things and win only on the question's context below.
    let same_name = |a: &Article| {
        let t = normalize_title(&a.title);
        t == norm || t.starts_with(&format!("{norm} ("))
    };
    cands.retain(|(_, a)| !(lowercase && qualified(a)));
    if cands.is_empty() {
        return None;
    }
    cands.sort_by_key(|(_, a)| (!same_name(a), std::cmp::Reverse(a.popularity)));
    let floor = cands[0].1.popularity / 10;
    cands.retain(|(_, a)| a.popularity >= floor);
    if cands.len() < 2 {
        return cands.pop();
    }
    let texts: Vec<String> = cands.iter().map(|(_, a)| format!("{}: {}", a.title, a.oneliner.as_deref().unwrap_or(""))).collect();
    let Ok(scores) = rr.score(query, &texts) else { return Some(cands.swap_remove(0)) };
    let top = (0..cands.len()).max_by(|&i, &j| scores[i].total_cmp(&scores[j])).unwrap_or(0);
    let i = if scores[top] >= scores[0] + DISAMBIG_MARGIN { top } else { 0 };
    Some(cands.swap_remove(i))
}

/// A disambiguated title such as "Mercury (planet)".
fn qualified(a: &Article) -> bool {
    a.title.ends_with(')') && a.title.contains(" (")
}

fn is_wikipedia(pack_id: &str) -> bool {
    pack_id.starts_with("enwiki") || pack_id == "simplewiki"
}

/// Title-cased like a name ("Golden Gate Bridge"): every word of 4+ letters starts uppercase.
fn proper_name(title: &str) -> bool {
    title.split(" (").next().unwrap_or(title).split_whitespace().filter(|w| w.chars().count() >= 4).all(|w| w.starts_with(|c: char| c.is_uppercase()))
}
