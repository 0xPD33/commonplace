"""English Wikipedia from the public monthly XML dumps (dumps.wikimedia.org/enwiki/<date>/, no account) → packbuild input.

The dump holds wikitext, so this module renders the templates that carry content itself: {{convert}}, dates,
{{lang}}, fractions, currency, lists; reference, maintenance and navigation templates render to nothing.
Infobox fields become a separate "Infobox" passage. QIDs come from the same dump's page_props table,
redirects from its redirect pages.

  convert  download the dump parts one at a time (resumable), render them on all cores into one Parquet shard
           per part, delete each part afterwards unless --keep
  build    merge the shards into a work dir: articles, passages, redirects, QIDs, pageview popularity

  python -m commonplace_pipeline.wikipedia convert --date 20260901 --out data/work/enwiki-20260901
  python -m commonplace_pipeline.wikipedia build --work data/work/enwiki-20260901 --pageviews data/raw/pageviews.sqlite
"""

from __future__ import annotations

import argparse
import bz2
import json
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from multiprocessing import Pool
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import wikitextparser as wtp

from .chunk import chunk_article, pageviews
from .packwrite import PackWriter
from .wikivoyage import BR_RE, DROP_RE, HATNOTE_RE, HEADING_RE, drop_files, load_qids

DUMPS = "https://dumps.wikimedia.org/enwiki"
# Wikimedia refuses generic client user agents (403).
UA = "commonplace-packbuild/0.1 (offline Wikipedia pack builder; monthly dump download)"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
UNITS = {"km": "km", "mi": "mi", "m": "m", "ft": "ft", "cm": "cm", "mm": "mm", "in": "in", "yd": "yd", "nmi": "nmi",
         "km2": "km²", "sqkm": "km²", "m2": "m²", "sqmi": "sq mi", "mi2": "sq mi", "ha": "ha", "acre": "acres",
         "kg": "kg", "g": "g", "t": "t", "lb": "lb", "st": "st", "oz": "oz", "C": "°C", "F": "°F", "K": "K",
         "km/h": "km/h", "mph": "mph", "kn": "knots", "m3": "m³", "l": "L", "L": "L", "hp": "hp", "kW": "kW", "MW": "MW",
         "m3/s": "m³/s", "cuft/s": "cu ft/s", "ftlb": "ft·lb", "Nm": "N·m", "psi": "psi", "kPa": "kPa", "ly": "light-years",
         "AU": "AU", "pc": "pc"}
RANGE = {"to", "-", "–", "and", "or", "by", "x", "×", "+/-", "±", "and(-)", "to(-)"}
FIRST_ARG = {"nowrap", "nobr", "small", "smaller", "big", "large", "em", "strong", "sic", "math", "mvar", "sub", "sup",
             "abbr", "keypress", "code", "var", "ill", "interlanguage link", "lang-rtl", "linktext", "not a typo", "flag",
             "flagcountry", "flagu", "country", "tooltip", "visible anchor", "vanchor", "anchor+", "title case", "gaps",
             "ship", "sclass", "ussc", "wikt", "pslink"}
SECOND_ARG = {"lang", "transl", "transliteration"}
LISTS = {"ubl", "unbulleted list", "plainlist", "flatlist", "hlist", "bulleted list", "bull list", "ordered list",
         "collapsible list", "cslist", "enum"}
DATES = {"birth date", "death date", "birth date and age", "death date and age", "start date", "end date", "dob",
         "start date and age", "end date and age", "film date", "date", "birth-date", "death-date", "dts", "bda", "dda",
         "death date and given age", "birth year and age", "death year and age", "release date"}
DASH = {"ndash": "–", "snd": " – ", "spaced ndash": " – ", "mdash": "—", "spaced mdash": " — ", "nbsp": " ", "thinsp": " ",
        "'": "'", "=": "=", "!": "|", "·": " · ", "dot": " · ", "middot": " · ", "bullet": " • ", "en dash": "–"}
DISAMB = {"disambiguation", "disambig", "dab", "disamb", "hndis", "geodis", "numberdis", "schooldis", "hospitaldis",
          "mountainindex", "roaddis", "shipindex", "surname", "given name", "set index article", "sia"}
INFOBOX_SKIP = re.compile(r"image|caption|alt|map|logo|signature|embed|module|footnote|seal|flag_|coat|pushpin|"
                          r"width|size|upright|native_name_lang|coordinates|coord|website|url|_ref$|^ref", re.I)


def plain(s: str) -> str:
    return wtp.parse(s).plain_text(replace_templates=render_template, replace_tables=render_table).strip()


def pos_args(t: wtp.Template) -> list[str]:
    return [plain(a.value) for a in t.arguments if a.positional]


def date(y: str, m: str = "", d: str = "") -> str:
    y, m, d = y.strip(), m.strip(), d.strip()
    if not y.lstrip("-").isdigit():
        return y
    mon = MONTHS[int(m) - 1] if m.isdigit() and 1 <= int(m) <= 12 else ""
    return " ".join(x for x in (d if d.isdigit() else "", mon, y) if x)


# {{convert}} default output: unit code → (display unit, factor, output code). Temperatures are special-cased.
CONV = {"m": ("ft", 3.28084, "ft"), "ft": ("m", 0.3048, "m"), "km": ("mi", 0.621371, "mi"), "mi": ("km", 1.609344, "km"),
        "cm": ("in", 0.393701, "in"), "in": ("cm", 2.54, "cm"), "km2": ("sq mi", 0.386102, "sqmi"),
        "sqkm": ("sq mi", 0.386102, "sqmi"), "sqmi": ("km²", 2.589988, "km2"), "mi2": ("km²", 2.589988, "km2"),
        "ha": ("acres", 2.471054, "acre"), "acre": ("ha", 0.404686, "ha"), "kg": ("lb", 2.204623, "lb"),
        "lb": ("kg", 0.453592, "kg"), "m3/s": ("cu ft/s", 35.31467, "cuft/s"), "km/h": ("mph", 0.621371, "mph"),
        "mph": ("km/h", 1.609344, "km/h"), "C": ("°F", 0, "F"), "F": ("°C", 0, "C")}


def converted(value: str, unit: str, out: str = "") -> str | None:
    """The second value of {{convert}}, rounded to the input's significant figures, at least 2 (2017 m → 6,617 ft)."""
    v = value.replace(",", "")
    if unit not in CONV or (out and out not in (CONV[unit][2], "abbr=on", "abbr=off")):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    u, f, _ = CONV[unit]
    y = x * 9 / 5 + 32 if unit == "C" else (x - 32) * 5 / 9 if unit == "F" else x * f
    digits = v.replace(".", "").lstrip("-0") if "." in v else v.lstrip("-0").rstrip("0")
    y = float(f"{y:.{max(len(digits), 2)}g}")
    return f"{y:,.{len(v.split('.')[1]) if '.' in v else 0}f} {u}" if abs(y) < 1000 else f"{round(y):,} {u}"


def render_convert(p: list[str]) -> str:
    if len(p) >= 4 and p[1] in RANGE:
        word = "to" if p[1] in ("-", "to(-)") else p[1]
        a, b = converted(p[0], p[3], p[4] if len(p) > 4 else ""), converted(p[2], p[3], p[4] if len(p) > 4 else "")
        extra = f" ({a.rsplit(' ', 1)[0]} {word} {b})" if a and b else ""
        return f"{p[0]} {word} {p[2]} {UNITS.get(p[3], p[3])}{extra}"
    if len(p) >= 2:
        c = converted(p[0], p[1], p[2] if len(p) > 2 else "")
        return f"{p[0]} {UNITS.get(p[1], p[1])}" + (f" ({c})" if c else "")
    return p[0] if p else ""


def render_template(t: wtp.Template) -> str:
    n = t.normal_name(capitalize=False).lower().replace("_", " ").strip()
    if n.startswith("infobox") or n.startswith("#"):
        return ""
    p = pos_args(t)
    first = p[0] if p else ""
    if n in ("convert", "cvt"):
        return render_convert(p)
    if n in DATES:
        if n in ("birth year and age", "death year and age"):
            return first
        dp = [x for x in p if x and not x.startswith(("df", "mf"))]
        if len(dp) >= 3 and all(x.lstrip("-").isdigit() for x in dp[:3]):
            return date(dp[0], dp[1], dp[2])
        return date(*dp[:2]) if dp else ""
    if n in DASH:
        return DASH[n]
    if n in FIRST_ARG:
        return first
    if n in SECOND_ARG or n == "nihongo":
        return p[1] if n in SECOND_ARG and len(p) > 1 else (f"{first} ({p[1]})" if len(p) > 1 and first else first)
    if n.startswith("lang-") or n.startswith("in lang"):
        return first if n.startswith("lang-") else ""
    if n in LISTS:
        return ", ".join(x for x in p if x)
    if n in ("frac", "sfrac", "fraction") and p:
        return f"1/{p[0]}" if len(p) == 1 else f"{p[0]}/{p[1]}" if len(p) == 2 else f"{p[0]} {p[1]}/{p[2]}"
    if n in ("circa", "c.", "ca"):
        return f"c. {first}".strip()
    if n in ("as of", "asof"):
        return f"As of {date(*p[:3])}" if p else ""
    if n in ("us$", "usd", "us dollar"):
        return f"US${first}"
    if n in ("val",):
        u = t.get_arg("u") or t.get_arg("ul")
        e = t.get_arg("e")
        return f"{first}{'×10^' + plain(e.value) if e else ''}{' ' + plain(u.value) if u else ''}"
    if n in ("formatnum", "format number", "fmt"):
        return first
    if n in ("quote", "blockquote", "cquote", "quote box", "poem quote"):
        a = t.get_arg("text") or t.get_arg("quote")
        return plain(a.value) if a else first
    if n in ("inflation",):
        return ""
    # Currency templates named by code ({{GBP|5}}, {{EUR|5}}); single-letter names ({{c|…}}) are not currency.
    if len(n) == 3 and n.isalpha() and first[:1].isdigit():
        return f"{first} {n.upper()}"
    return ""


def render_table(table: wtp.Table) -> str:
    try:
        rows = table.data()
    except (IndexError, ValueError):
        return ""
    lines = [("| " + " | ".join(" ".join(plain(c or "").replace("|", "/").split()) for c in r) + " |") for r in rows]
    return "\n" + "\n".join(l for l in lines if l.strip("| ")) + "\n"


def infobox(parsed: wtp.WikiText) -> str | None:
    for t in parsed.templates:
        if t.normal_name(capitalize=False).lower().startswith("infobox"):
            lines = []
            for a in t.arguments:
                k = a.name.strip()
                if a.positional or not k or INFOBOX_SKIP.search(k):
                    continue
                v = " ".join(plain(BR_RE.sub(", ", DROP_RE.sub("", a.value))).split())
                if v and len(v) < 300:
                    lines.append(f"{k.replace('_', ' ')}: {v}")
            return "\n".join(lines[:30]) or None
    return None


def to_markdown(wikitext: str) -> str:
    lead, sep, rest = wikitext.partition("\n==")
    text = plain(BR_RE.sub(" ", DROP_RE.sub("", drop_files(HATNOTE_RE.sub("", lead) + sep + rest))))
    out: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if m := HEADING_RE.match(s):
            out.append(f"{'#' * len(m.group(1))} {m.group(2)}")
        elif s[:1] in ("*", "#", ";"):
            if body := s.lstrip("*#;: ").strip():
                out.append(f"- {body}")
        elif s.startswith(":"):
            if body := s.lstrip(": ").strip():
                out.append(body)
        else:
            out.append(s)
    return "\n".join(out)


def render_page(page: tuple[int, str, int, str]) -> dict | None:
    pid, title, rev, text = page
    try:
        ib = infobox(wtp.parse(text[:60_000]))
        chunks = chunk_article(title, to_markdown(text), None) + ([("Infobox", ib)] if ib else [])
    except Exception:  # noqa: BLE001 — one malformed page must not stop a 7M-page run
        return None
    if not chunks:
        return None
    return {"page_id": pid, "title": title, "revision": rev, "passages": [{"section_path": s, "text": x} for s, x in chunks]}


def iter_dump(path: Path, redirects: list[tuple[str, str]]):
    """Yield (page_id, title, revision, wikitext) for main-namespace articles; collect redirects on the side."""
    for _, el in ET.iterparse(bz2.open(path), events=("end",)):
        if not el.tag.endswith("}page"):
            continue
        if el.findtext("{*}ns") == "0" and (title := el.findtext("{*}title")):
            red = el.find("{*}redirect")
            text = el.findtext("{*}revision/{*}text") or ""
            if red is not None:
                redirects.append((title, red.get("title", "")))
            elif title != "Main Page":
                names = {x.lower().strip().replace("_", " ") for x in re.findall(r"\{\{\s*([^|}\n]+)", text[-3000:] + text[:3000])}
                if "(disambiguation)" not in title and not names & DISAMB:
                    yield int(el.findtext("{*}id")), title, int(el.findtext("{*}revision/{*}id") or 0), text
        el.clear()


def dump_files(date: str) -> list[tuple[str, str]]:
    """(file name, url) of the pages-articles-multistream parts, in page-id order."""
    st = httpx.get(f"{DUMPS}/{date}/dumpstatus.json", headers={"User-Agent": UA}, timeout=60).raise_for_status().json()
    job = st["jobs"]["articlesmultistreamdump"]
    if job["status"] != "done":
        raise SystemExit(f"{date}: pages-articles dump is {job['status']}")
    files = [f for f in job["files"] if re.search(r"multistream\d+\.xml-p\d+p\d+\.bz2$", f)]
    files.sort(key=lambda f: int(re.search(r"multistream(\d+)", f).group(1)))
    return [(f, f"{DUMPS}/{date}/{f}") for f in files]


def cmd_convert(a) -> None:
    out = Path(a.out)
    (out / "shards").mkdir(parents=True, exist_ok=True)
    raw = Path(a.raw)
    raw.mkdir(parents=True, exist_ok=True)
    (out / "dump.json").write_text(json.dumps({"date": a.date, "source": f"{DUMPS}/{a.date}/"}))
    props = raw / f"enwiki-{a.date}-page_props.sql.gz"
    if not props.exists():
        subprocess.run(["curl", "-fsSL", "-A", UA, "--retry", "5", "-C", "-", "-o", f"{props}.part", f"{DUMPS}/{a.date}/{props.name}"], check=True)
        Path(f"{props}.part").rename(props)
    with Pool(a.jobs) as pool:
        for i, (name, url) in enumerate(dump_files(a.date)):
            shard = out / "shards" / f"{i:03d}.parquet"
            if shard.exists():
                continue
            f = raw / name
            if not f.exists():
                subprocess.run(["curl", "-fsSL", "-A", UA, "--retry", "5", "-C", "-", "-o", f"{f}.part", url], check=True)
                Path(f"{f}.part").rename(f)
            t0, redirects = time.time(), []
            rows = [r for r in pool.imap(render_page, iter_dump(f, redirects), chunksize=32) if r]
            pq.write_table(pa.table({"from_title": [r[0] for r in redirects], "to_title": [r[1] for r in redirects]}),
                           out / "shards" / f"{i:03d}.redirects.parquet", compression="zstd")
            pq.write_table(pa.Table.from_pylist(rows), shard.with_suffix(".tmp"), compression="zstd")
            shard.with_suffix(".tmp").rename(shard)
            if not a.keep:
                f.unlink()
            print(f"{name}: {len(rows)} articles, {sum(len(r['passages']) for r in rows)} passages, "
                  f"{len(redirects)} redirects ({time.time() - t0:.0f}s)", flush=True)


def cmd_build(a) -> None:
    work = Path(a.work)
    shards = sorted(s for s in (work / "shards").glob("*.parquet") if not s.name.endswith(".redirects.parquet"))
    date = json.loads((work / "dump.json").read_text())["date"]
    qids = load_qids(str(Path(a.raw) / f"enwiki-{date}-page_props.sql.gz"))
    titles = [t for s in shards for t in pq.read_table(s, columns=["title"])["title"].to_pylist()]
    views = iter(pageviews(a.pageviews, titles) if a.pageviews else [0] * len(titles))
    w = PackWriter(work)
    aid_of: dict[str, int] = {}
    page_ids, revisions = [], []
    for s in shards:  # one shard at a time: all of Wikipedia does not fit in memory as Python objects
        for r in pq.read_table(s).to_pylist():
            aid_of[r["title"]] = w.aid
            w.add(r["title"], [(p["section_path"], p["text"]) for p in r["passages"]], qid=qids.get(r["page_id"]),
                  popularity=next(views))
            page_ids.append(r["page_id"])
            revisions.append(r["revision"])
    w.close()
    red = pa.concat_tables([pq.read_table(s) for s in sorted((work / "shards").glob("*.redirects.parquet"))])
    keep = [(f, aid_of[t.split("#")[0]]) for f, t in zip(red["from_title"].to_pylist(), red["to_title"].to_pylist())
            if t.split("#")[0] in aid_of]
    pq.write_table(pa.table({"from_title": pa.array([k[0] for k in keep], pa.string()),
                             "article_id": pa.array([k[1] for k in keep], pa.uint32())}), work / "redirects.parquet", compression="zstd")
    pq.write_table(pa.table({"page_id": page_ids, "revision": revisions}), work / "revisions.parquet", compression="zstd")
    print(f"redirects: {len(keep)} of {red.num_rows} point to an article in the pack; QIDs: {sum(1 for p in page_ids if p in qids)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert")
    c.add_argument("--date", required=True, help="dump date, e.g. 20260901")
    c.add_argument("--out", required=True)
    c.add_argument("--raw", default="data/raw/enwiki-dump")
    c.add_argument("--jobs", type=int, default=16)
    c.add_argument("--keep", action="store_true", help="keep the downloaded dump parts")
    b = sub.add_parser("build")
    b.add_argument("--work", required=True)
    b.add_argument("--raw", default="data/raw/enwiki-dump")
    b.add_argument("--pageviews")
    a = ap.parse_args()
    {"convert": cmd_convert, "build": cmd_build}[a.cmd](a)


if __name__ == "__main__":
    main()
