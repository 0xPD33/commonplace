//! Stage 0: normalize, link entities. No LLM. The engine adds the question's intent and whether it follows
//! up the last turn (`encoders::Head`).

use crate::library::Library;
use crate::pack::{Pack, meta::Article, normalize_title};
use crate::encoders::Reranker;
use crate::text::STOPWORDS;
use std::collections::HashSet;

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
    /// The message needs the last turn to be understood ("why is it green?"); the engine sets it.
    pub needs_rewrite: bool,
    pub compare: bool,
    pub entities: Vec<Entity>,
}

/// Comparison wording, removed from a comparison's attribute: it names no property to search for.
const COMPARE_WORDS: &[&str] = &[" vs ", " vs. ", " versus ", "compare", "difference between", "differences between", "better than", "worse than", "similarities"];
/// Question-opening verbs: capitalized only because they start the sentence ("Compare the Nile…").
const LEADING_VERBS: &[&str] = &["compare", "explain", "describe", "define", "list", "name", "give", "show", "find", "summarize", "summarise"];

pub fn normalize(q: &str) -> String {
    q.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `rr` lets entity linking pick among same-name articles by the question's context. `topics` are the
/// article titles the rewrite model named; when any of them exists, they are the entities.
/// `scope` limits entity linking to those pack ids (empty: every pack).
pub fn route(lib: &Library, query: &str, rr: Option<&Reranker>, topics: &[String], scope: &[String]) -> Route {
    let query = normalize(query);
    let words: Vec<&str> = query.split_whitespace().collect();
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
    Route { query, complex, needs_rewrite: false, compare: false, entities }
}

/// Longest-first n-gram lookup against article titles and redirects in every pack.
/// Single words must be capitalized in the query (or the query must be very short) to limit noise.
pub fn link_entities(lib: &Library, query: &str, scope: &[String]) -> Vec<Entity> {
    link_entities_with(lib, query, None, scope)
}

/// Margin (reranker logits) a less popular same-name article needs to replace the most popular one.
const DISAMBIG_MARGIN: f32 = 1.0;

/// Reranker score (logits) that a redirect from a lowercase phrase holding a name needs: "capital of Hungary" →
/// Budapest scores 8.0 for its question, "live in Canada" → Billy Bragg discography 1.1.
const REDIRECT_MIN_SCORE: f32 = 5.0;

/// Weight of the log view ratio that a less viewed sense of the same name pays against the primary topic:
/// "Mars (2024 film)" has a sixth of the planet's views and pays 1.8 logits, so "How hot is Mars?" stays
/// with the planet. Longer titles ("Moon landing") pay nothing; they win on the question's context.
const VIEWS_PRIOR: f32 = 1.0;

/// Bonus (reranker logits) for a candidate whose Wikidata classes match those of the other things in the
/// question, times `kind_match`.
const KIND_BONUS: f32 = 2.0;

/// The reranker alone cannot tell "Mars" from "Mars Inc." in "Compare Mars and Venus": it scores each
/// name against the whole question. So the question's other names are read first (most viewed article, no
/// reranker); a candidate that shares their Wikidata class ("planet") then gets a bonus.
fn link_entities_with(lib: &Library, query: &str, rr: Option<&Reranker>, scope: &[String]) -> Vec<Entity> {
    if rr.is_none() || lib.wikidata.is_none() {
        return link_pass(lib, query, rr, scope, &[], false);
    }
    let first = link_pass(lib, query, None, scope, &[], false);
    let second = link_pass(lib, query, rr, scope, &first, false);
    // A name the reranker changed ("Python" the language) may change what the others mean ("Ruby").
    let same = |a: &[Entity], b: &[Entity]| a.len() == b.len() && a.iter().zip(b).all(|(x, y)| (x.pack, x.article.id) == (y.pack, y.article.id));
    if second.len() < 2 || same(&first, &second) { second } else { link_pass(lib, query, rr, scope, &second, false) }
}

/// The things a comparison names in lowercase ("Compare lions and tigers"): common nouns read as names, kept
/// when they share a Wikidata class with another one, so "population" drops out of "the population of malta
/// and cyprus".
pub fn compared_things(lib: &Library, query: &str, rr: Option<&Reranker>, scope: &[String]) -> Vec<Entity> {
    let found = link_pass(lib, query, rr, scope, &[], true);
    let kinds: Vec<HashSet<String>> = found.iter().map(|e| instance_of(lib, scope, &e.article)).collect();
    let paired = |i: usize| (0..kinds.len()).any(|j| j != i && kind_match(&kinds[i], &kinds[j]) > 0.0);
    found.into_iter().enumerate().filter(|(i, _)| paired(*i)).map(|(_, e)| e).collect()
}

/// `common_nouns` reads lowercase words as names in any message, through any redirect ("baking soda" → Sodium
/// bicarbonate), and keeps more than three of them; `compared_things` then filters them by class.
fn link_pass(lib: &Library, query: &str, rr: Option<&Reranker>, scope: &[String], context: &[Entity], common_nouns: bool) -> Vec<Entity> {
    let kinds: Vec<(String, HashSet<String>)> =
        context.iter().map(|e| (e.surface.to_lowercase(), instance_of(lib, scope, &e.article))).filter(|(_, k)| !k.is_empty()).collect();
    let toks: Vec<&str> = query
        .split(|c: char| c.is_whitespace() || matches!(c, '?' | '!' | ',' | ';' | ':' | '"'))
        .filter(|t| !t.is_empty())
        .collect();
    let short = common_nouns || toks.len() <= 3;
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
            // A phrase with a pronoun is part of the user's sentence ("what should I know before I go"), not the
            // film "Before I Go", unless the user writes it like a title.
            let pronoun = |w: &&str| matches!(w.trim_matches(|c: char| !c.is_alphanumeric()).to_lowercase().as_str(), "i" | "me" | "my" | "you" | "your" | "we" | "our");
            if n > 1 && span.iter().any(pronoun) && !proper_name(&span.join(" ")) {
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
            let lowercase = !surface.chars().any(char::is_uppercase);
            // A span that starts lowercase reaches a title that shares one of its words ("population of Japan");
            // other redirects only from a span holding a name, when the reranker ties them to the question
            // ("capital of Hungary" → Budapest, not "live in Canada" → Billy Bragg discography or "version" → DR-DOS).
            let named = span.iter().any(|w| w.chars().any(char::is_uppercase));
            let about = |a: &Article| {
                let doc = format!("{}: {}", a.title, a.oneliner.as_deref().unwrap_or(""));
                rr.is_some_and(|rr| rr.score(query, &[doc]).is_ok_and(|s| s.first().is_some_and(|&s| s >= REDIRECT_MIN_SCORE)))
            };
            let best = best.filter(|(_, a)| {
                common_nouns || first.chars().any(char::is_uppercase) || shares_word(&norm, &a.title) || (named && about(a))
            });
            // Lowercase phrases ("light bulb", "northern lights") are common nouns: they never resolve to a
            // qualified title like "Light Bulb (Abbott Elementary)". A capitalized name with no article of
            // its own ("Mercury") may still mean one of its "(…)" variants.
            let best = best.or_else(|| {
                let variant = format!("{norm} (");
                (!lowercase && !code)
                    .then(|| lookup(&|p| p.meta.candidates(&norm, 6).ok()?.into_iter().find(|a| normalize_title(&a.title).starts_with(&variant))))
                    .flatten()
                    .filter(|(_, a)| {
                        let views = |t: &str| lookup(&|p| p.meta.lookup_title(&normalize_title(t)).ok().flatten()).map(|(_, a)| a.popularity);
                        !outweighed_by_parts(span, &views, a.popularity)
                    })
            });
            let best = match (rr, best) {
                (Some(rr), Some(b)) if !code && n <= 3 => {
                    let others = other_names(&toks, i..i + n);
                    let mine = surface.to_lowercase();
                    let kinds: HashSet<String> = kinds.iter().filter(|(s, _)| *s != mine).flat_map(|(_, k)| k.iter().cloned()).collect();
                    disambiguate(lib, rr, query, &Mention { norm: &norm, lowercase, others: &others, kinds: &kinds }, b, scope)
                }
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
    found.truncate(if common_nouns { 6 } else { 3 });
    found
}

/// A follow-up that asks the last question again for another place or time ("And in the world?" after "What is
/// the tallest building in Europe?"): the last question with its final phrase of that preposition replaced.
pub fn reask(last: &str, message: &str) -> Option<String> {
    let msg = message.trim().trim_end_matches(['?', '.', '!']);
    let rest = ["and ", "what about ", "how about ", "or "]
        .iter()
        .find_map(|p| msg.get(..p.len()).filter(|h| h.eq_ignore_ascii_case(p)).map(|_| msg[p.len()..].trim()))?;
    let prep = rest.split_whitespace().next()?;
    if !matches!(prep, "in" | "on" | "at" | "for" | "of" | "from" | "during") {
        return None;
    }
    let last = last.trim().trim_end_matches(['?', '.', '!']);
    let at = last.rfind(&format!(" {prep} "))?;
    Some(format!("{} {rest}?", &last[..at]))
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
    // Fragments of one or two letters are what a removed name or an apostrophe leaves ("viruses" → "es", "what's" → "s").
    rest.split(|c: char| !c.is_alphanumeric())
        .filter(|w| w.chars().count() > 2 && !STOPWORDS.contains(w) && !matches!(*w, "or" | "vs" | "versus" | "which" | "both" | "between"))
        .collect::<Vec<_>>()
        .join(" ")
}

/// Capitalized words of the question outside `span`: the other things it names ("Mars" in "Compare Mars
/// with Venus"). A title that contains one is about both, not about the span alone.
fn other_names(toks: &[&str], span: std::ops::Range<usize>) -> Vec<String> {
    let words = toks.iter().enumerate().filter(|(j, _)| !span.contains(j)).map(|(_, t)| t.trim_matches(|c: char| !c.is_alphanumeric()));
    let names = words.filter(|w| w.chars().next().is_some_and(char::is_uppercase)).map(str::to_lowercase);
    names.filter(|w| !STOPWORDS.contains(w.as_str()) && !LEADING_VERBS.contains(&w.as_str())).collect()
}

/// A work named "X and Y" ("Venus and Mars (Wings album)") is not what the words "Venus and Mars" mean
/// when each part has an article that more people read. `views` gives the views of a part's article.
fn outweighed_by_parts(span: &[&str], views: &dyn Fn(&str) -> Option<u64>, work_views: u64) -> bool {
    let word = |w: &str| w.trim_matches(|c: char| !c.is_alphanumeric()).to_lowercase();
    let parts: Vec<String> = span.split(|w| STOPWORDS.contains(word(w).as_str())).map(|p| p.join(" ")).collect();
    parts.len() >= 2 && parts.iter().all(|p| views(p).is_some_and(|v| v > work_views))
}

/// What a span of the question may mean: its normalized text, whether it is a lowercase common noun, the
/// other names in the question (a title containing one is about both, not about this span) and their
/// Wikidata classes.
struct Mention<'a> {
    norm: &'a str,
    lowercase: bool,
    others: &'a [String],
    kinds: &'a HashSet<String>,
}

/// An article's Wikidata "instance of" classes ("inner planet of the Solar System", "river"), lowercase;
/// empty when unknown.
fn instance_of(lib: &Library, scope: &[String], a: &Article) -> HashSet<String> {
    let wd = lib.wikidata.as_ref().filter(|(m, _)| scope.is_empty() || scope.contains(&m.pack_id));
    let (Some((_, wd)), Some(qid)) = (wd, a.qid.as_deref()) else { return HashSet::new() };
    let facts = wd.facts(qid, 1).unwrap_or_default();
    facts.iter().filter(|f| f.pid == "P31").flat_map(|f| f.value.split(", ")).map(str::to_lowercase).collect()
}

/// Whether two articles share a Wikidata class ("planet" for Mars and Venus); false when either is unknown.
pub fn same_kind(lib: &Library, scope: &[String], a: &Article, b: &Article) -> bool {
    kind_match(&instance_of(lib, scope, a), &instance_of(lib, scope, b)) > 0.0
}

/// How well two sets of classes match: 1 when they share a class ("u.s. state"), 0.5 when they share only
/// a word ("programming language" in "JVM language" and "object-oriented programming language"), else 0.
fn kind_match(a: &HashSet<String>, b: &HashSet<String>) -> f32 {
    let words = |s: &HashSet<String>| -> HashSet<String> {
        s.iter().flat_map(|c| c.split(|ch: char| !ch.is_alphanumeric())).filter(|w| w.chars().count() >= 4).map(str::to_string).collect()
    };
    if !a.is_disjoint(b) {
        1.0
    } else if !words(a).is_disjoint(&words(b)) {
        0.5
    } else {
        0.0
    }
}

/// Pick among Wikipedia articles sharing a name ("Mercury", "Mercury (planet)", "Mercury (automobile)").
/// Start from the most viewed same-name article (the primary topic), leave out rarely viewed ones
/// (disambiguation stubs), and let the question's context override only by a clear reranker margin.
/// A lowercase span (a common noun) rules out qualified titles; then nothing may be left.
fn disambiguate(lib: &Library, rr: &Reranker, query: &str, m: &Mention, best: (u8, Article), scope: &[String]) -> Option<(u8, Article)> {
    let primary = (best.0, best.1.id);
    let mut cands = vec![best];
    for (pi, p) in lib.scoped(scope).filter(|(_, p)| is_wikipedia(&p.manifest.pack_id)) {
        // A sense that the first rows miss ("Ruby (programming language)" behind people named Ruby) joins
        // when its class matches the other names.
        let senses = if m.kinds.is_empty() { vec![] } else { p.meta.senses(m.norm, 12).unwrap_or_default() };
        let senses = senses.into_iter().filter(|a| kind_match(&instance_of(lib, scope, a), m.kinds) > 0.0);
        for a in p.meta.candidates(m.norm, 6).unwrap_or_default().into_iter().chain(senses) {
            if !cands.iter().any(|(cp, c)| *cp == pi && c.id == a.id) {
                cands.push((pi, a));
            }
        }
    }
    // Same name ("Bridge", "Bridge (music)") competes on views; longer titles ("Amazon River") are other things
    // and win only on the question's context below. A lowercase common noun names the thing itself: no
    // qualified or longer title ("discovered" is not "Discovered attack").
    let same_name = |pi: u8, a: &Article| {
        let t = normalize_title(&a.title);
        (pi, a.id) == primary || t == m.norm || t.starts_with(&format!("{} (", m.norm))
    };
    let names_other = |a: &Article| normalize_title(&a.title).split(|c: char| !c.is_alphanumeric()).any(|w| m.others.iter().any(|o| o == w));
    cands.retain(|(pi, a)| !(m.lowercase && (qualified(a) || !same_name(*pi, a))) && ((*pi, a.id) == primary || !names_other(a)));
    if cands.is_empty() {
        return None;
    }
    cands.sort_by_key(|(pi, a)| (!same_name(*pi, a), std::cmp::Reverse(a.popularity)));
    let floor = cands[0].1.popularity / 10;
    cands.retain(|(_, a)| a.popularity >= floor);
    if cands.len() < 2 {
        return cands.pop();
    }
    let texts: Vec<String> = cands.iter().map(|(_, a)| format!("{}: {}", a.title, a.oneliner.as_deref().unwrap_or(""))).collect();
    let Ok(scores) = rr.score(query, &texts) else { return Some(cands.swap_remove(0)) };
    let views = |a: &Article| a.popularity.max(1) as f32;
    let scores: Vec<f32> = scores
        .iter()
        .zip(&cands)
        .map(|(s, (pi, a))| {
            let kind = if m.kinds.is_empty() { 0.0 } else { KIND_BONUS * kind_match(&instance_of(lib, scope, a), m.kinds) };
            let prior = if same_name(*pi, a) { VIEWS_PRIOR * (views(a) / views(&cands[0].1)).ln().min(0.0) } else { 0.0 };
            s + kind + prior
        })
        .collect();
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

/// Title-cased like a name of two words or more ("Golden Gate Bridge"): every word of 4+ letters starts
/// uppercase. A one-word title that a phrase reaches is a redirect ("river flows" → River).
fn proper_name(title: &str) -> bool {
    let words: Vec<&str> = title.split(" (").next().unwrap_or(title).split_whitespace().collect();
    words.len() > 1 && words.iter().filter(|w| w.chars().count() >= 4).all(|w| w.starts_with(|c: char| c.is_uppercase()))
}

/// Some word of `norm` other than a stopword starts like a word of the title (four letters, so "rivers"
/// shares River).
fn shares_word(norm: &str, title: &str) -> bool {
    let t = normalize_title(title.split(" (").next().unwrap_or(title));
    let words = |s: &str| s.split(|c: char| !c.is_alphanumeric()).filter(|w| !w.is_empty()).map(str::to_string).collect::<Vec<_>>();
    let title_words = words(&t);
    words(norm).iter().filter(|w| !STOPWORDS.contains(w.as_str())).any(|w| {
        let stem: String = w.chars().take(4).collect();
        title_words.iter().any(|x| x.starts_with(&stem))
    })
}
