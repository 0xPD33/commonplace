#!/usr/bin/env python3
"""In-app eval run on the Pixel: install a .spike APK, pick the model, ask every dev question as a new topic,
then write <label>.answers.jsonl (for eval/import.py) and <label>.tel.jsonl (telemetry) next to this script.

usage: phone_eval.py <label> <apk> <gemma|ling|minicpm> [queries.jsonl]
"""
import json, os, re, subprocess, sys, time
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(os.environ.get("OUT", "/tmp/commonplace-bench"))
PKG = "app.commonplace.spike"
PREFS = {"gemma": {"engine": "tpu"}, "ling": {"engine": "cpu", "model": "fast"}, "minicpm": {"engine": "cpu", "model": "small"}}
# `input text` types ASCII only.
ASCII = {"—": "-", "–": "-", "°": " degrees ", "’": "'", "“": '"', "”": '"', "é": "e"}


def adb(*a, check=True):
    return subprocess.run(["adb", *a], capture_output=True, text=True, check=check).stdout


def sh(cmd, check=True):
    return adb("shell", cmd, check=check)


def nodes():
    sh("uiautomator dump /sdcard/ui.xml", check=False)
    xml = adb("exec-out", "cat", "/sdcard/ui.xml", check=False)
    try:
        root = ET.fromstring(xml[xml.find("<?xml"):] if "<?xml" in xml else xml)
    except ET.ParseError:
        return {}
    out = {}
    for n in root.iter("node"):
        for k in (n.get("resource-id", "").split("/")[-1], n.get("text", ""), n.get("content-desc", "")):
            if k and k not in out:
                x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds")))
                out[k] = ((x1 + x2) // 2, (y1 + y2) // 2)
    return out


def tap(key, tries=5):
    for _ in range(tries):
        n = nodes()
        if key in n:
            sh(f"input tap {n[key][0]} {n[key][1]}")
            return True
        time.sleep(1)
    return False


def typed(q):
    q = "".join(ASCII.get(c, c) for c in q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def ask(q, timeout=180):
    tap("new_topic", tries=2)
    time.sleep(1)
    if not tap("ask_input"):
        raise RuntimeError("no ask_input")
    text = typed(q).replace("'", "'\\''").replace(" ", "%s")
    sh(f"input text '{text}'")
    time.sleep(1)
    if not tap("ask_send"):
        raise RuntimeError("no ask_send")
    time.sleep(3)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if "ask_stop" not in nodes():
            return
        time.sleep(1)
    print(f"  timeout on: {q}", flush=True)


def skin():
    t = 0.0
    for line in sh("dumpsys thermalservice", check=False).splitlines():
        if "mName=VIRTUAL-SKIN-LEGACY," in line and "Temperature{" in line:
            m = re.search(r"mValue=([0-9.]+)", line)
            if m:
                t = float(m.group(1))
    return t


def cool(limit=38.0, wait=300):
    t0 = time.time()
    while skin() > limit and time.time() - t0 < wait:
        time.sleep(10)
    print(f"  skin {skin():.1f} C", flush=True)


def main():
    label, apk, model = sys.argv[1:4]
    HERE.mkdir(parents=True, exist_ok=True)
    qfile = Path(sys.argv[4] if len(sys.argv) > 4 else str(Path(__file__).resolve().parents[2] / "eval/dev.jsonl"))
    qs = [json.loads(l) for l in qfile.open()]
    sh(f"am force-stop {PKG}")
    print(adb("install", "-r", apk).strip().splitlines()[-1], flush=True)
    sh(f"cmd package compile -m speed-profile -f {PKG}")
    time.sleep(90)
    prefs = "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n<map>\n" + "".join(
        f'    <string name="{k}">{v}</string>\n' for k, v in PREFS[model].items()) + "</map>\n"
    (HERE / "prefs.xml").write_text(prefs)
    adb("push", str(HERE / "prefs.xml"), "/data/local/tmp/cp-prefs.xml")
    sh(f"run-as {PKG} cp /data/local/tmp/cp-prefs.xml shared_prefs/commonplace.xml")
    convs0 = set(sh(f"run-as {PKG} ls files/conversations", check=False).split())
    n_tel0 = len(sh(f"run-as {PKG} cat files/telemetry.jsonl", check=False).splitlines())
    sh("cmd statusbar collapse", check=False)
    sh(f"am start -W -n {PKG}/app.commonplace.MainActivity")
    time.sleep(8)
    cool()
    print(f"[{label}] warm-up", flush=True)
    ask("What is the capital of France?", timeout=240)
    for i, q in enumerate(qs, 1):
        print(f"[{label}] {i}/{len(qs)} {q['id']}", flush=True)
        try:
            ask(q["query"])
        except RuntimeError as e:
            print(f"  {e}; retry", flush=True)
            time.sleep(3)
            ask(q["query"])
    time.sleep(2)
    tel = sh(f"run-as {PKG} cat files/telemetry.jsonl").splitlines()[n_tel0:]
    (HERE / f"{label}.tel.jsonl").write_text("\n".join(tel) + "\n")
    by_q = {typed(q["query"]).rstrip("?").lower(): q["id"] for q in qs}
    rows = {}
    for f in sorted(set(sh(f"run-as {PKG} ls files/conversations").split()) - convs0):
        d = json.loads(sh(f"run-as {PKG} cat files/conversations/{f}"))
        for t in d.get("turns", []):
            qid = by_q.get(t["query"].replace("\\", "").rstrip("?").lower())
            if not qid:
                continue
            ans = "".join(s.get("text") or s.get("unverified") or (f"[{s['cite']}]" if "cite" in s else "") for s in t.get("segments") or [])
            rows[qid] = {"id": qid, "answer": ans, "segments": t.get("segments"), "sources": t.get("sources", [])}
    for line in tel:
        r = json.loads(line)
        qid = by_q.get(r.get("query", "").replace("\\", "").rstrip("?").lower())
        if qid in rows:
            rows[qid]["record"] = {k: r.get(k) for k in ("ttft_ms", "total_ms", "card_ms", "thermal_headroom", "model_id", "synthesis", "build")}
    (HERE / f"{label}.answers.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows.values()))
    print(f"[{label}] {len(rows)} of {len(qs)} answers, {len(tel)} telemetry lines", flush=True)


if __name__ == "__main__":
    main()
