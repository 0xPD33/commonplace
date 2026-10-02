"""Shared helpers for the eval scripts. Run every script from the devshell."""

import hashlib
import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVAL = REPO / "eval"
RUNS = EVAL / "runs"
BASELINES = EVAL / "baselines"
BIN = REPO / "core/target/release/commonplace"
LIBRARY = "data/library"
DEFAULT_MODEL = "data/models/llm/Ling-3.0-tiny-Q4_0.gguf"
ABLATIONS = ["no-dense", "no-sparse", "no-rerank", "no-cards", "no-planner", "no-doc2query", "no-llm"]
CATEGORIES = [
    "factual", "explanation", "comparison", "multihop", "numeric",
    "travel", "howto", "out_of_corpus", "long_tail",
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def git_describe():
    r = subprocess.run(["git", "describe", "--always", "--dirty", "--tags"],
                       cwd=REPO, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else "no-commits"


def norm_title(s):
    """Python twin of commonplace_core::pack::normalize_title."""
    return " ".join(s.replace("_", " ").split()).lower()


def library_packs(library):
    packs = []
    for m in sorted((REPO / library / "packs").glob("*/manifest.json")):
        d = json.loads(m.read_text())
        packs.append({
            "pack_id": d.get("pack_id"),
            "pack_type": d.get("pack_type"),
            "title": d.get("title"),
            "snapshot_date": d.get("snapshot_date"),
            "build_date": d.get("build_date"),
            "counts": d.get("counts"),
            "embedder": d.get("embedder"),
            "manifest_sha256": sha256(m),
        })
    return packs


def load_queries(path, subset=None):
    """`subset` is a comma list; each token matches a query id or a category."""
    qs = read_jsonl(path)
    if subset:
        keys = {t.strip() for t in subset.split(",") if t.strip()}
        qs = [q for q in qs if q["id"] in keys or q.get("category") in keys]
    return qs


def cli_flags(meta_flags):
    return [f"--{f}" for f in meta_flags]


def claude_version():
    r = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    return r.stdout.strip()


def claude_json(prompt, system, schema, model, tools="", budget_usd=1.0):
    """One headless `claude -p` call with structured output. Returns (structured, raw).

    --safe-mode and a temp cwd keep the user's CLAUDE.md, hooks, plugins and MCP
    servers out of the eval; --system-prompt replaces the Claude Code agent prompt.
    """
    cmd = [
        "claude", "-p", "--safe-mode", "--no-session-persistence",
        "--output-format", "json", "--model", model,
        "--system-prompt", system, "--json-schema", json.dumps(schema),
        "--max-budget-usd", str(budget_usd), "--tools", tools,
    ]
    if tools:
        cmd += ["--allowedTools", tools, "--permission-mode", "dontAsk"]
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=900)
    try:
        raw = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude exit {r.returncode}: {r.stderr.strip() or r.stdout[:500]}")
    if raw.get("is_error") or "structured_output" not in raw:
        raise RuntimeError(f"claude {raw.get('subtype')}: {str(raw.get('result'))[:500]}")
    return raw["structured_output"], raw


def claude_meta(raw):
    # WebSearch runs through a Haiku helper call, so the answering model is the one that
    # wrote the most tokens, and search counts must be summed over every model.
    mu = raw.get("modelUsage", {})
    return {
        "model": max(mu, key=lambda m: mu[m].get("outputTokens", 0)) if mu else None,
        "models_used": sorted(mu),
        "cost_usd": raw.get("total_cost_usd"),
        "duration_ms": raw.get("duration_ms"),
        "num_turns": raw.get("num_turns"),
        "web_search_requests": sum(m.get("webSearchRequests", 0) for m in mu.values()),
    }
