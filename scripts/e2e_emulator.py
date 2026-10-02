#!/usr/bin/env python3
"""End-to-end run of the Android app on a device or emulator, driven through adb + uiautomator.

It walks the main user flows, takes a screenshot at each step, and writes
artifacts/e2e/<stamp>/{NN-step.png, report.json, index.html} for a person to review.

Usage (inside `nix develop`, with the emulator running and the APK built):
  python3 scripts/e2e_emulator.py [--install] [--push simplewiki lfm25-1.2b] [--question "..."]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

PKG = "app.commonplace"
ROOT = Path(__file__).resolve().parent.parent
APK = ROOT / "android/app/build/outputs/apk/debug/app-debug.apk"


def adb(*args: str, check: bool = True, capture: bool = True) -> str:
    r = subprocess.run(["adb", *args], capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout if capture else ""


class Ui:
    def __init__(self) -> None:
        self.root: ET.Element | None = None

    def dump(self) -> ET.Element:
        for _ in range(5):
            out = adb("exec-out", "uiautomator", "dump", "/dev/tty", check=False)
            xml = out[out.find("<?xml") : out.rfind("</hierarchy>") + len("</hierarchy>")]
            if xml:
                self.root = ET.fromstring(xml)
                return self.root
            time.sleep(0.5)
        raise RuntimeError("uiautomator dump failed")

    def nodes(self) -> list[ET.Element]:
        return list(self.dump().iter("node"))

    def find(self, tag: str | None = None, text: str | None = None, contains: str | None = None) -> ET.Element | None:
        for n in self.nodes():
            rid = n.get("resource-id", "")
            t = n.get("text", "") or n.get("content-desc", "")
            if tag and rid != tag and not rid.endswith(":id/" + tag):
                continue
            if text is not None and t != text:
                continue
            if contains is not None and contains not in t:
                continue
            return n
        return None

    def all_text(self) -> str:
        return "\n".join(filter(None, (n.get("text") for n in self.nodes())))

    @staticmethod
    def center(n: ET.Element) -> tuple[int, int]:
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds", "[0,0][0,0]")))
        return (x1 + x2) // 2, (y1 + y2) // 2

    def tap(self, n: ET.Element) -> None:
        x, y = self.center(n)
        adb("shell", "input", "tap", str(x), str(y))

    def scroll_find(self, tries: int = 6, **kw) -> ET.Element:
        for _ in range(tries):
            n = self.find(**kw)
            if n is not None:
                return n
            adb("shell", "input", "swipe", "540", "1800", "540", "700", "300")
            time.sleep(0.8)
        raise TimeoutError(f"not found after scrolling: {kw}")

    def wait(self, timeout: float, **kw) -> ET.Element:
        end = time.time() + timeout
        while time.time() < end:
            n = self.find(**kw)
            if n is not None:
                return n
            time.sleep(0.7)
        raise TimeoutError(f"timed out waiting for {kw}")


class Run:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.steps: list[dict] = []
        self.ui = Ui()

    def shot(self, name: str, note: str = "") -> None:
        path = self.out / f"{len(self.steps) + 1:02d}-{name}.png"
        path.write_bytes(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)
        self.steps.append({"step": name, "ok": True, "screenshot": path.name, "note": note})
        print(f"  ✓ {name} {note}")

    def fail(self, name: str, err: Exception) -> None:
        path = self.out / f"{len(self.steps) + 1:02d}-{name}-FAILED.png"
        path.write_bytes(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)
        self.steps.append({"step": name, "ok": False, "screenshot": path.name, "note": str(err)})
        print(f"  ✗ {name}: {err}")

    def back(self) -> None:
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.0)

    def type_text(self, text: str) -> None:
        # `input text` needs spaces escaped as %s.
        adb("shell", "input", "text", text.replace(" ", "%s").replace("'", "\\'").replace("?", "\\?"))


def ask_and_wait(r: Run, question: str, label: str, timeout: float, tap_input: bool = True) -> dict:
    """`tap_input=False` types into the focused input after the text already there (a chip's draft)."""
    ui = r.ui
    if tap_input:
        ui.tap(ui.wait(20, tag="ask_input"))
    r.type_text(question)
    ui.tap(ui.wait(10, tag="ask_send"))
    return wait_answer(r, question, label, timeout)


def wait_answer(r: Run, question: str, label: str, timeout: float) -> dict:
    ui = r.ui
    t0 = time.time()
    # The new turn shows the question. Small talk can finish before the stop button is ever seen.
    ui.wait(15, tag="turn_query", contains=question)
    time.sleep(3)
    r.shot(f"{label}-streaming", f"{time.time() - t0:.1f}s after asking")
    end = t0 + timeout
    while ui.find(tag="ask_stop") is not None:
        if time.time() > end:
            raise TimeoutError("answer did not finish")
        time.sleep(1)
    total_s = time.time() - t0
    shown = [n.get("text") for n in ui.nodes() if n.get("resource-id", "").endswith("timings")]
    timings = shown[-1] if shown else None
    r.shot(f"{label}-answer", f"done after {total_s:.1f}s" + (f" · {timings}" if timings else ""))
    return {"question": question, "total_s": round(total_s, 1), "app_timings": timings}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="install the debug APK first")
    ap.add_argument("--push", nargs="*", default=[], help="pack ids to dev-push before starting")
    ap.add_argument("--question", default="Compare the Nile and the Amazon")
    ap.add_argument("--followup", default="Which one is longer?")
    ap.add_argument("--compare-with", default="the Mississippi River", help="typed after the Compare with… draft")
    ap.add_argument("--answer-timeout", type=float, default=240)
    ap.add_argument("--import-dir", help="dist dir of a split pack (packbuild split): test the SAF import with it")
    ap.add_argument("--catalog-dir", help="split packs plus the catalog.json built into the APK (see the catalog step): test Get more and Install from Downloads")
    ap.add_argument("--withhold", default="", help="file of --catalog-dir that is missing at the first install and added for the second")
    ap.add_argument("--only", nargs="*", default=[], help="run only these steps")
    args = ap.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "artifacts/e2e" / stamp
    out.mkdir(parents=True, exist_ok=True)
    r = Run(out)
    device = adb("shell", "getprop", "ro.product.model").strip()
    print(f"device: {device} → {out}")

    if args.install:
        adb("install", "-r", "-g", str(APK))
    if args.push:
        subprocess.run([str(ROOT / "scripts/dev-push.sh"), *args.push], check=True)
    adb("shell", "am", "force-stop", "com.android.documentsui")
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    results: dict = {"device": device, "stamp": stamp, "questions": []}

    ui = r.ui
    flows = []

    def step(name):
        def deco(fn):
            flows.append((name, fn))
            return fn

        return deco

    if args.import_dir:

        @step("import")
        def _():
            files = sorted(Path(args.import_dir).iterdir())
            pack_id = next(f.name for f in files if f.name.endswith(".pack.json")).removesuffix(".pack.json")
            adb("shell", "rm", "-rf", "/sdcard/Download/*")
            for f in files:
                adb("push", "-q", str(f), "/sdcard/Download/")
            adb("shell", "run-as", PKG, "rm", "-rf", f"files/library/packs/{pack_id}")
            adb("shell", "am", "force-stop", PKG)
            adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
            ui.tap(ui.wait(30, tag="open_library"))
            ui.tap(ui.wait(10, tag="import_pack"))
            # Files pushed over adb are not in the picker's Recent list; open the Downloads root.
            first = None
            for _ in range(3):
                ui.tap(ui.wait(15, text="Show roots"))
                time.sleep(1.5)
                ui.tap(ui.wait(10, text="Downloads"))
                try:
                    first = ui.wait(8, text=files[0].name)
                    break
                except TimeoutError:
                    pass
            if first is None:
                raise TimeoutError("pack files not shown in the Downloads root")
            x, y = ui.center(first)
            adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "900")
            for f in files[1:]:
                ui.tap(ui.wait(10, text=f.name))
            r.shot("import-picker", f"{len(files)} files selected")
            ui.tap(ui.wait(10, text="Select"))
            t0 = time.time()
            ui.wait(600, text="Delete the downloaded files?")
            r.shot("import-done", f"verified and installed in {time.time() - t0:.1f}s")
            ui.tap(ui.wait(10, text="Delete"))
            ui.wait(10, contains="Deleted")
            left = adb("shell", "ls", "/sdcard/Download/").split()
            if left:
                raise RuntimeError(f"files left in Downloads: {left}")
            r.back()

    if args.catalog_dir:

        @step("catalog")
        def _():
            # The APK carries a test catalog of two packs; a third pack in the directory is not in it.
            d = Path(args.catalog_dir)
            catalog = json.loads((d / "catalog.json").read_text())["packs"]
            files = sorted(f.name for f in d.iterdir() if f.is_file() and f.name != "catalog.json")
            ids = sorted(f.removesuffix(".pack.json") for f in files if f.endswith(".pack.json"))
            outside = [i for i in ids if i not in {p["pack_id"] for p in catalog}]
            adb("shell", "rm", "-rf", "/sdcard/Download/*")
            for f in files:
                if f != args.withhold:
                    adb("push", "-q", str(d / f), "/sdcard/Download/")
            adb("shell", "sh", "-c", "'echo unrelated > /sdcard/Download/notes.txt'")
            for i in ids:
                adb("shell", "run-as", PKG, "rm", "-rf", f"files/library/packs/{i}")
            adb("shell", "am", "force-stop", PKG)
            adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
            ui.tap(ui.wait(60, tag="open_library"))
            ui.wait(10, tag="library_list")
            ui.wait(10, tag="available_row")
            text = ui.all_text()
            for p in catalog:
                if p["title"] not in text:
                    raise RuntimeError(f"{p['title']} is not listed under Get more")
            if "Starter" not in text:
                raise RuntimeError("no Starter tag")
            r.shot("catalog-available", f"Get more lists {len(catalog)} packs")

            ui.tap(ui.wait(10, tag="available_row"))
            ui.wait(10, tag="pack_sheet")
            ui.wait(10, contains="Download every file below")
            buttons = [n for n in ui.nodes() if n.get("resource-id", "").endswith("download_file")]
            first = next(p for p in catalog if p["recommended"])
            if len(buttons) != len(first["files"]):
                raise RuntimeError(f"{len(buttons)} download buttons for {len(first['files'])} files")
            r.shot("catalog-sheet", f"{first['title']}: {len(buttons)} files")
            ui.tap(buttons[0])
            time.sleep(2)
            r.shot("catalog-download", "Download opens the URL in a browser, or says there is none")
            r.back()
            if ui.find(tag="pack_sheet") is None:
                raise RuntimeError("the sheet is gone after the browser")
            ui.tap(ui.wait(10, tag="sheet_install"))

            def select_all():
                ui.wait(20, contains=".pack.json")
                node = ui.find(contains=".pack.json")
                x, y = ui.center(node)
                adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "900")
                ui.tap(ui.wait(10, text="More options"))
                ui.tap(ui.wait(10, text="Select all"))
                time.sleep(1)
                r.shot("catalog-picker", "all files selected")
                ui.tap(ui.wait(10, text="Select"))

            select_all()
            for _ in range(8):
                if ui.find(tag="import_progress") is not None:
                    r.shot("catalog-progress")
                    break
            t0 = time.time()
            ui.wait(180, text="Delete the downloaded files?")
            r.shot("catalog-imported", f"after {time.time() - t0:.1f}s of waiting")
            ui.tap(ui.wait(10, text="Delete"))
            ui.wait(10, contains="Deleted")
            note = " ".join(n.get("text", "") for n in ui.wait(10, tag="error_note").iter("node"))
            if args.withhold and args.withhold not in note:
                raise RuntimeError(f"missing file not named: {note!r}")
            r.shot("catalog-missing", note.replace("\n", " "))
            installed = adb("shell", "run-as", PKG, "ls", "files/library/packs").split()
            incomplete = {p["pack_id"] for p in catalog if args.withhold in {f["name"] for f in p["files"]}}
            want = [i for i in ids if i not in incomplete]
            if [i for i in ids if i in installed] != want:
                raise RuntimeError(f"installed {installed}, expected {want} among {ids}")
            left = sorted(adb("shell", "ls", "/sdcard/Download/").split())
            print("  left in Downloads:", left)

            if args.withhold:
                adb("push", "-q", str(d / args.withhold), "/sdcard/Download/")
                ui.tap(ui.wait(10, tag="import_pack"))
                select_all()
                ui.wait(180, text="Delete the downloaded files?")
                ui.tap(ui.wait(10, text="Delete"))
                ui.wait(10, contains="Deleted")
                time.sleep(1)
                installed = adb("shell", "run-as", PKG, "ls", "files/library/packs").split()
                if not set(ids) <= set(installed):
                    raise RuntimeError(f"after the second install: {installed}")
                if ui.find(tag="error_note") is not None:
                    raise RuntimeError("problems are still shown")
                left = sorted(adb("shell", "ls", "/sdcard/Download/").split())
                if left != ["notes.txt"]:
                    raise RuntimeError(f"left in Downloads: {left}")
                r.shot("catalog-complete", "the missing part was added and installed; notes.txt stays")
            ui.wait(10, tag="library_list")
            r.back()

    @step("welcome")
    def _():
        ui.wait(60, tag="welcome")
        ui.wait(120, contains="Ready")
        r.shot("welcome", ui.find(tag="model_status").get("text"))

    @step("offline-sheet")
    def _():
        ui.tap(ui.wait(10, tag="offline_badge"))
        ui.wait(10, contains="Private by construction")
        r.shot("offline-sheet")
        r.back()

    @step("first-question")
    def _():
        results["questions"].append(ask_and_wait(r, args.question, "q1", args.answer_timeout))

    @step("source-passage")
    def _():
        row = ui.scroll_find(tag="sources_row")
        # First card in the sources row.
        child = next(n for n in row.iter("node") if n is not row and n.get("clickable") == "true")
        ui.tap(child)
        ui.wait(15, tag="passage_view")
        r.shot("passage")
        ui.tap(ui.scroll_find(tag="open_article"))
        ui.wait(15, tag="article_view")
        time.sleep(1)
        r.shot("article")
        r.back()
        r.back()

    @step("follow-up")
    def _():
        results["questions"].append(ask_and_wait(r, args.followup, "q2", args.answer_timeout))

    @step("retry")
    def _():
        ui.tap(ui.scroll_find(tag="retry"))
        t0 = time.time()
        ui.wait(15, tag="ask_stop")
        while ui.find(tag="ask_stop") is not None:
            if time.time() > t0 + args.answer_timeout:
                raise TimeoutError("retried answer did not finish")
            time.sleep(1)
        r.shot("retry", f"new answer after {time.time() - t0:.1f}s")

    @step("compare-chip")
    def _():
        # "Compare with…" drafts "Compare <topic> with " into the focused input; finish and send it.
        ui.tap(ui.scroll_find(tag="compare_with"))
        draft = ui.wait(10, tag="ask_input").get("text", "")
        if not draft.startswith("Compare "):
            raise RuntimeError(f"unexpected draft {draft!r}")
        results["questions"].append(ask_and_wait(r, args.compare_with, "compare", args.answer_timeout, tap_input=False))
        ui.scroll_find(tag="follow_up_chips")
        related = [n.get("text") for n in ui.nodes() if n.get("resource-id", "").endswith("related_topic")]
        r.shot("follow-up-chips", f"related: {related or 'none'}")

    @step("shorter-chip")
    def _():
        # A reformat: the latest answer rewritten shorter, with no new search.
        ui.tap(ui.scroll_find(text="Shorter"))
        results["questions"].append(wait_answer(r, "Make it shorter", "shorter", args.answer_timeout))

    @step("small-talk")
    def _():
        results["questions"].append(ask_and_wait(r, "thanks!", "small-talk", args.answer_timeout))

    @step("stop")
    def _():
        ui.tap(ui.wait(20, tag="ask_input"))
        r.type_text(args.question)
        ui.tap(ui.wait(10, tag="ask_send"))
        ui.tap(ui.wait(15, tag="ask_stop"))
        ui.wait(60, text="Stopped")
        ui.wait(10, tag="ask_send")
        r.shot("stopped", "stop keeps the partial answer and offers retry")

    @step("library")
    def _():
        ui.tap(ui.wait(10, tag="open_library"))
        ui.wait(10, tag="library_list")
        r.shot("library")
        r.back()

    @step("settings")
    def _():
        ui.tap(ui.wait(10, tag="overflow"))
        ui.tap(ui.wait(10, text="Settings"))
        ui.wait(10, tag="settings")
        r.shot("settings")
        ui.tap(ui.scroll_find(tag="open_diagnostics"))
        ui.wait(10, tag="diagnostics")
        time.sleep(1)
        r.shot("diagnostics")
        for _ in range(3):
            r.back()
            try:
                ui.wait(3, tag="ask_input")
                break
            except TimeoutError:
                pass

    @step("new-topic")
    def _():
        ui.tap(ui.wait(10, tag="new_topic"))
        ui.wait(10, tag="welcome")
        r.shot("new-topic", "recent questions listed")

    @step("history")
    def _():
        # The conversation before "new topic" is saved: find it by an answer word and open it again.
        ui.tap(ui.wait(10, tag="open_history"))
        ui.wait(10, tag="history_list")
        ui.tap(ui.wait(10, tag="history_search"))
        r.type_text("Mississippi")
        time.sleep(1)
        r.shot("history", "search: Mississippi")
        row = next(n for n in ui.wait(10, tag="history_list").iter("node") if n.get("clickable") == "true")
        ui.tap(row)
        # The list opens at the last saved turn: the small-talk step's "thanks!".
        ui.wait(15, tag="turn_query", contains="thanks")
        r.shot("history-reopened", "the saved conversation is back")
        ui.tap(ui.wait(10, tag="new_topic"))
        ui.wait(10, tag="welcome")

    @step("dark-mode")
    def _():
        adb("shell", "cmd", "uimode", "night", "yes")
        try:
            time.sleep(2)
            ui.wait(10, tag="welcome")
            r.shot("dark-welcome")
            ui.tap(ui.wait(10, tag="open_library"))
            ui.wait(10, tag="library_list")
            r.shot("dark-library")
            r.back()
        finally:
            adb("shell", "cmd", "uimode", "night", "no")

    for name, fn in flows:
        if args.only and name not in args.only:
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - record and continue so one failure keeps the rest of the report
            r.fail(name, e)
            adb("shell", "am", "force-stop", "com.android.documentsui")
            adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
            time.sleep(2)
            for _ in range(4):
                if ui.find(tag="ask_input") is not None or ui.find(tag="welcome") is not None:
                    break
                r.back()

    crashes = adb("logcat", "-d", "-b", "crash", check=False)
    results["crash_log"] = crashes.strip()[-4000:]
    results["steps"] = r.steps
    results["passed"] = all(s["ok"] for s in r.steps) and PKG not in crashes
    (out / "report.json").write_text(json.dumps(results, indent=2))
    cards = "\n".join(
        f'<figure><img src="{s["screenshot"]}"><figcaption>{"✓" if s["ok"] else "✗"} {html.escape(s["step"])}<br>'
        f"<small>{html.escape(s['note'])}</small></figcaption></figure>"
        for s in r.steps
    )
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Commonplace E2E</title>"
        "<style>body{font:14px system-ui;margin:16px}figure{display:inline-block;width:270px;margin:8px;vertical-align:top}"
        "img{width:100%;border:1px solid #ccc;border-radius:12px}</style>"
        f"<h1>Commonplace E2E · {html.escape(device)} · {stamp}</h1>"
        f"<p>{'PASSED' if results['passed'] else 'FAILED'} · {html.escape(json.dumps(results['questions']))}</p>{cards}"
    )
    print(f"{'PASSED' if results['passed'] else 'FAILED'} → {out / 'index.html'}")
    return 0 if results["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
