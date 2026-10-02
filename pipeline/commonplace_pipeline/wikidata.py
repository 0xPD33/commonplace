"""Wikidata facts (PLAN §6.5) from the permutans/wikidata-* Parquet tables (see scripts/fetch-wikidata.sh).

Stage 1 (pyarrow, one process per claims file) keeps statements on the properties in
configs/wikidata-props.tsv and flattens them. Stage 2 (DuckDB) keeps items with an enwiki sitelink,
picks one value per (item, property), and writes the packbuild inputs to --out:
props.parquet, entities.parquet, facts.parquet, and title_qid.parquet (enwiki title -> QID).
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

PROPS_TSV = Path(__file__).resolve().parents[1] / "configs" / "wikidata-props.tsv"
UNIT_PROPS = ["P5061", "P498"]  # unit symbol, ISO 4217 currency code: they name the units of quantities
# Common units whose Wikidata symbol reads badly ("a" for year) or whose item may be missing from the tables.
UNIT_OVERRIDE = {
    "Q11573": "m", "Q828224": "km", "Q174728": "cm", "Q174789": "mm", "Q712226": "km²", "Q25343": "m²", "Q35852": "ha",
    "Q11570": "kg", "Q41803": "g", "Q191118": "t", "Q11574": "s", "Q7727": "min", "Q25235": "h", "Q573": "days",
    "Q23387": "weeks", "Q5151": "months", "Q577": "years", "Q1092296": "years", "Q11229": "%", "Q25267": "°C",
    "Q42289": "°F", "Q11579": "K", "Q3710": "ft", "Q218593": "in", "Q253276": "mi", "Q232291": "mi²",
    "Q794261": "m³/s", "Q25250": "V", "Q4917": "USD", "Q4916": "EUR", "Q25224": "GBP", "Q8146": "JPY",
}
MAX_ITEMS = 6
FLAT = pa.schema([
    ("qid", pa.string()), ("pid", pa.string()), ("datatype", pa.string()), ("rank", pa.string()), ("ord", pa.int64()),
    ("vid", pa.string()), ("vstr", pa.string()), ("text", pa.string()), ("language", pa.string()),
    ("amount", pa.string()), ("unit", pa.string()), ("time", pa.string()), ("prec", pa.int64()),
    ("lat", pa.float64()), ("lon", pa.float64()), ("pit", pa.string()), ("has_end", pa.bool_()),
])


def load_props() -> list[tuple[str, str]]:
    rows = [ln.split("\t") for ln in PROPS_TSV.read_text().splitlines() if ln and not ln.startswith("#")]
    return [(p.strip(), label.strip()) for p, label in rows]


def qualifier(q: pa.Array, pid: str, n: int) -> tuple[pa.Array, np.ndarray]:
    """(time of the first snak of qualifier pid, or null; whether the row has qualifier pid) per row."""
    flat, parent = pc.list_flatten(q), pc.list_parent_indices(q)
    m = pc.equal(pc.struct_field(flat, "key"), pid)
    rows = parent.filter(m).to_numpy()
    snak = pc.list_element(pc.struct_field(flat.filter(m), "value"), 0)
    times = pc.struct_field(pc.struct_field(snak, "datavalue"), "time")
    idx = np.full(n, -1)
    idx[rows] = np.arange(len(rows))
    present = np.zeros(n, bool)
    present[rows] = True
    return times.take(pa.array(idx, mask=idx < 0)), present


def flatten_claims(args: tuple[str, str, list[str]]) -> int:
    src, dst, keep = args
    f = pq.ParquetFile(src)
    keep_set = pa.array(keep)
    cols = ["id", "property", "datatype", "rank", "datavalue", "qualifiers"]
    w, total, ord0 = None, 0, 0
    for i in range(f.num_row_groups):
        t = f.read_row_group(i, columns=cols)
        base = ord0
        ord0 += t.num_rows
        t = t.append_column("ord", pa.array(np.arange(base, ord0)))
        t = t.filter(pc.and_(pc.is_in(t["property"], value_set=keep_set), pc.not_equal(t["rank"], "deprecated")))
        if not t.num_rows:
            continue
        dv, q, n = t["datavalue"].combine_chunks(), t["qualifiers"].combine_chunks(), t.num_rows
        sf = lambda name: pc.struct_field(dv, name)  # noqa: E731
        num = lambda name: pc.coalesce(pc.struct_field(sf(name), f"{name}__number"),  # noqa: E731
                                       pc.cast(pc.struct_field(sf(name), f"{name}__integer"), pa.float64()))
        pit, _ = qualifier(q, "P585", n)
        _, has_end = qualifier(q, "P582", n)
        out = pa.Table.from_arrays([
            pc.cast(t["id"], pa.string()), pc.cast(t["property"], pa.string()), pc.cast(t["datatype"], pa.string()),
            pc.cast(t["rank"], pa.string()), t["ord"],
            *(pc.cast(sf(c), pa.string()) for c in ["id", "datavalue__string", "text", "language", "amount", "unit", "time"]),
            pc.struct_field(sf("precision"), "precision__integer"), num("latitude"), num("longitude"),
            pc.cast(pit, pa.string()), pa.array(has_end),
        ], schema=FLAT)
        if w is None:
            w = pq.ParquetWriter(dst + ".tmp", FLAT, compression="zstd")
        w.write_table(out)
        total += n
    if w is None:
        pq.write_table(FLAT.empty_table(), dst + ".tmp")
    else:
        w.close()
    Path(dst + ".tmp").rename(dst)
    return total


SQL = r"""
CREATE MACRO ordinal(n) AS n::VARCHAR || CASE WHEN n % 100 IN (11, 12, 13) THEN 'th' WHEN n % 10 = 1 THEN 'st'
  WHEN n % 10 = 2 THEN 'nd' WHEN n % 10 = 3 THEN 'rd' ELSE 'th' END;
CREATE MACRO year_str(y) AS CASE WHEN y < 0 THEN (-y)::VARCHAR || ' BC' ELSE y::VARCHAR END;
CREATE MACRO t_y(t) AS TRY_CAST(regexp_extract(t, '^([+-]?\d+)-', 1) AS BIGINT);
CREATE MACRO t_m(t) AS TRY_CAST(regexp_extract(t, '^[+-]?\d+-(\d\d)', 1) AS INT);
CREATE MACRO t_d(t) AS TRY_CAST(regexp_extract(t, '^[+-]?\d+-\d\d-(\d\d)', 1) AS INT);
CREATE MACRO month_name(m) AS ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
  'September', 'October', 'November', 'December'][m];
CREATE MACRO fmt_time(t, p) AS CASE
  WHEN t_y(t) IS NULL THEN NULL
  WHEN p >= 11 AND t_d(t) > 0 AND t_m(t) > 0 THEN t_d(t)::VARCHAR || ' ' || month_name(t_m(t)) || ' ' || year_str(t_y(t))
  WHEN p >= 10 AND t_m(t) > 0 THEN month_name(t_m(t)) || ' ' || year_str(t_y(t))
  WHEN p = 8 AND t_y(t) > 0 THEN (t_y(t) // 10 * 10)::VARCHAR || 's'
  WHEN p = 7 THEN ordinal((abs(t_y(t)) - 1) // 100 + 1) || ' century' || CASE WHEN t_y(t) < 0 THEN ' BC' ELSE '' END
  ELSE year_str(t_y(t)) END;

-- DISTINCT: the 2026-09-29 upload repeats rows across overlapping chunk files.
CREATE TABLE enwiki AS SELECT DISTINCT id AS qid, title FROM read_parquet('{raw}/wikidata-links/enwiki/*.parquet')
  WHERE NOT regexp_matches(title, '^(Category|Template|Wikipedia|Portal|Module|Help|Draft|File|MediaWiki|TimedText|Book):');
CREATE TABLE claims AS SELECT * FROM read_parquet('{flat}/*.parquet');
CREATE TABLE lab AS
  SELECT id, arg_min(value, CASE language WHEN 'en' THEN 0 WHEN 'mul' THEN 1 ELSE 2 END) AS label FROM (
    SELECT id, language, value FROM read_parquet(['{raw}/wikidata-labels/en/*.parquet', '{raw}/wikidata-labels/mul/*.parquet'])
    UNION ALL
    SELECT ref, 'claims', label FROM read_parquet('{raw}/wikidata-claims_labels/en/*.parquet') WHERE field <> 'property-labels'
  ) GROUP BY id;
CREATE TABLE unit_name AS
  SELECT qid, coalesce(min(vstr) FILTER (pid = 'P498'),
                       arg_min(text, CASE language WHEN 'en' THEN 0 WHEN 'mul' THEN 1 ELSE 2 END) FILTER (pid = 'P5061')) AS unit
  FROM claims WHERE pid IN ('P498', 'P5061') GROUP BY qid;

CREATE TABLE vals AS
SELECT c.qid, c.pid, c.datatype, c.ord, c.pit, c.has_end,
  CASE c.rank WHEN 'preferred' THEN 2 ELSE 1 END AS rank,
  CASE WHEN c.datatype = 'quantity' THEN TRY_CAST(c.amount AS DOUBLE) END AS value_num,
  CASE WHEN c.datatype = 'quantity' AND c.unit <> '1' THEN coalesce(o.unit, u.unit, ul.label) END AS unit,
  CASE c.datatype
    WHEN 'quantity' THEN NULL
    WHEN 'wikibase-item' THEN vl.label
    WHEN 'time' THEN fmt_time(c.time, c.prec)
    WHEN 'globe-coordinate' THEN printf('%.4f°%s, %.4f°%s', abs(c.lat), CASE WHEN c.lat >= 0 THEN 'N' ELSE 'S' END,
                                        abs(c.lon), CASE WHEN c.lon >= 0 THEN 'E' ELSE 'W' END)
    WHEN 'monolingualtext' THEN c.text
    ELSE c.vstr END AS value_text
FROM claims c
JOIN enwiki e ON e.qid = c.qid
JOIN props p ON p.pid = c.pid
LEFT JOIN lab vl ON vl.id = c.vid
LEFT JOIN unit_override o ON o.qid = regexp_extract(c.unit, 'Q\d+$')
LEFT JOIN unit_name u ON u.qid = regexp_extract(c.unit, 'Q\d+$')
LEFT JOIN lab ul ON ul.id = regexp_extract(c.unit, 'Q\d+$')
WHERE c.datatype <> 'monolingualtext' OR c.language IN ('en', 'mul');

CREATE TABLE facts AS
WITH ok AS (
  SELECT * FROM vals WHERE value_num IS NOT NULL OR value_text IS NOT NULL
  QUALIFY rank = max(rank) OVER (PARTITION BY qid, pid)
), cur AS (
  SELECT * FROM ok
  WINDOW w AS (PARTITION BY qid, pid)
  QUALIFY has_end::INT = min(has_end::INT) OVER w AND coalesce(pit, '') = max(coalesce(pit, '')) OVER w
)
SELECT qid, pid,
  arg_min(value_num, ord) AS value_num,
  CASE WHEN any_value(datatype) IN ('wikibase-item', 'monolingualtext')
       THEN array_to_string(list(DISTINCT value_text)[1:{max_items}], ', ')
       ELSE arg_min(value_text, ord) END AS value_text,
  arg_min(unit, ord) AS unit,
  year_str(t_y(any_value(pit))) AS point_in_time,
  any_value(rank)::UINTEGER AS rank
FROM cur GROUP BY qid, pid ORDER BY qid, pid;

CREATE TABLE entities AS
SELECT e.qid, coalesce(l.label, e.title) AS label, a.aliases
FROM (SELECT qid, min(title) AS title FROM enwiki GROUP BY qid) e
LEFT JOIN lab l ON l.id = e.qid
LEFT JOIN (SELECT id, string_agg(DISTINCT value, '|') AS aliases
           FROM read_parquet(['{raw}/wikidata-aliases/en/*.parquet', '{raw}/wikidata-aliases/mul/*.parquet'])
           GROUP BY id) a ON a.id = e.qid
WHERE e.qid IN (SELECT DISTINCT qid FROM facts) ORDER BY e.qid;

COPY (SELECT pid, label, priority::UINTEGER AS priority FROM props ORDER BY priority) TO '{out}/props.parquet' (COMPRESSION zstd);
COPY facts TO '{out}/facts.parquet' (COMPRESSION zstd);
COPY entities TO '{out}/entities.parquet' (COMPRESSION zstd);
COPY (SELECT title, qid FROM enwiki ORDER BY title) TO '{out}/title_qid.parquet' (COMPRESSION zstd);
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw/wikidata", help="output of scripts/fetch-wikidata.sh")
    ap.add_argument("--out", default="data/work/wikidata")
    ap.add_argument("--jobs", type=int, default=11)
    ap.add_argument("--memory", default="24GB", help="DuckDB memory limit")
    args = ap.parse_args()
    raw, out = Path(args.raw), Path(args.out)
    flat = out / "claims-flat"
    flat.mkdir(parents=True, exist_ok=True)
    props = load_props()
    keep = [p for p, _ in props] + UNIT_PROPS

    t0 = time.time()
    srcs = sorted((raw / "wikidata-claims" / "all").glob("*.parquet"))
    todo = [(str(s), str(flat / s.name), keep) for s in srcs if not (flat / s.name).exists()]
    with ProcessPoolExecutor(args.jobs) as ex:
        for (src, _, _), n in zip(todo, ex.map(flatten_claims, todo)):
            print(f"stage 1: {Path(src).name}: {n} statements kept ({time.time() - t0:.0f}s)")

    con = duckdb.connect()
    con.execute(f"SET memory_limit = '{args.memory}'; SET temp_directory = '{out / 'tmp'}'")
    con.register("props_in", pa.table({"pid": [p for p, _ in props], "label": [l for _, l in props],
                                       "priority": list(range(len(props)))}))
    con.execute("CREATE TABLE props AS SELECT * FROM props_in")
    con.register("unit_override_in", pa.table({"qid": list(UNIT_OVERRIDE), "unit": list(UNIT_OVERRIDE.values())}))
    con.execute("CREATE TABLE unit_override AS SELECT * FROM unit_override_in")
    for stmt in SQL.format(raw=raw, flat=flat, out=out, max_items=MAX_ITEMS).split(";\n"):
        if stmt.strip():
            con.execute(stmt)
    for name in ["facts", "entities", "enwiki", "claims"]:
        print(f"{name}: {con.execute(f'SELECT count(*) FROM {name}').fetchone()[0]} rows")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
