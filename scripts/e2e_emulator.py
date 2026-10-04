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
import tarfile
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


FIXTURES = ROOT / "scripts/fixtures"
# The text of scripts/fixtures/quillfeather.pdf, one string per page. The island and its people are invented, so
# only the added document can answer a question about them.
DOC_PAGES = [
    "Quillfeather Lighthouse: a field guide\n\nThe Quillfeather Lighthouse stands on a granite spur above the northern shore of the invented island of Marrowby. The tower was finished in 1887 and is forty-one metres tall. Its builders cut the blocks from the spur itself, so the tower looks like a grey tooth growing out of the rock. The first lamp burned whale oil. In 1931 the keepers changed to paraffin, and in 1962 the light became electric. Sailors call the beam the Quill because it sweeps the water in a thin white stroke every nine seconds.\n\nVisitors reach the lighthouse by a footpath of two hundred and twelve steps that starts at the old ferry pier. The climb takes about twenty minutes. A small museum in the oil store shows lamps, logbooks and the fog bell. The museum opens from May to September and is closed on Mondays.",
    "The keepers of Quillfeather\n\nThe first head keeper of the Quillfeather Lighthouse was Odalys Brennmark. She arrived in 1887 with her brother and kept the light for thirty-three years. Her logbooks record every ship that passed, the wind, and the state of the lamp. The logbooks also record that she taught the children of the island to read in the lamp room on winter evenings.\n\nAfter Brennmark retired in 1920, the post went to Tobiah Wrenfield, who served until the light was automated in 1989. Wrenfield kept bees on the south slope. The honey of the Quillfeather bees is still sold at the museum shop, and the label shows a small drawing of the tower.",
    "The great storm and the fog bell\n\nOn the night of the ninth of November 1913, a storm that the islanders still call the Grey Week drove the steamer Halcyon Marsh onto the Marrowby shoals. The keepers saw the first rockets at midnight. Brennmark lit a second lamp in the gallery and rang the fog bell by hand for eleven hours so that the lifeboat from Skerry Quay could find the channel. All forty-seven passengers and crew reached the shore alive.\n\nThe fog bell weighs ninety kilograms and still hangs beside the door of the museum. Visitors may ring it once. The rope was replaced in 2004, and the brass plate under the bell lists the names of the forty-seven people who were saved.",
]
DOC_QUESTION = "Quillfeather Lighthouse Odalys Brennmark"


def subtree_text(n: ET.Element) -> str:
    return " | ".join(filter(None, ((d.get("text") or d.get("content-desc") or "") for d in n.iter("node"))))


def new_topic(r: Run) -> None:
    ui = r.ui
    n = ui.find(tag="new_topic")
    if n is not None:
        ui.tap(n)
        ui.wait(10, tag="welcome")


def open_library(r: Run) -> None:
    r.ui.tap(r.ui.wait(60, tag="open_library"))
    r.ui.wait(10, tag="library_list")


def pack_row(ui: Ui, title: str) -> ET.Element | None:
    for n in ui.dump().iter("node"):
        if n.get("resource-id", "").endswith("pack_row") and title in subtree_text(n):
            return n
    return None


def scroll_to_row(r: Run, title: str) -> ET.Element:
    ui = r.ui
    for _ in range(10):
        row = pack_row(ui, title)
        if row is not None:
            return row
        adb("shell", "input", "swipe", "540", "1800", "540", "900", "300")
        time.sleep(0.8)
    raise TimeoutError(f"no pack row with {title!r}")


def row_child(row: ET.Element, tag: str | None = None, desc: str | None = None) -> ET.Element:
    for d in row.iter("node"):
        if tag and d.get("resource-id", "").endswith(tag):
            return d
        if desc and d.get("content-desc") == desc:
            return d
    raise RuntimeError(f"no {tag or desc} in the row: {subtree_text(row)}")


def set_switch(r: Run, title: str, on: bool) -> None:
    """Tap the row's switch and wait until the row shows the new state ("Off: not searched" when off)."""
    ui = r.ui
    row = scroll_to_row(r, title)
    if ("Off: not searched" not in subtree_text(row)) == on:
        raise RuntimeError(f"{title} is already {'on' if on else 'off'}")
    ui.tap(row_child(row, tag="pack_switch"))
    end = time.time() + 30
    while time.time() < end:
        row = pack_row(ui, title)
        if row is not None and ("Off: not searched" not in subtree_text(row)) == on:
            return
        time.sleep(0.7)
    raise TimeoutError(f"{title} did not switch {'on' if on else 'off'}")


def remove_row(r: Run, title: str) -> None:
    """Remove through the row's menu and the confirmation dialog (its own window: no resource ids)."""
    ui = r.ui
    ui.tap(row_child(scroll_to_row(r, title), desc="Actions"))
    ui.tap(ui.wait(5, text="Remove"))
    ui.wait(5, contains=f"Remove {title}?")
    ui.tap(ui.wait(5, text="Remove"))


def ask_card(r: Run, question: str, label: str, timeout: float = 150) -> dict:
    """Ask on a fresh topic, wait for the answer card, stop the answer, and return the card and source text."""
    ui = r.ui
    new_topic(r)
    ui.tap(ui.wait(20, tag="ask_input"))
    r.type_text(question)
    ui.tap(ui.wait(10, tag="ask_send"))
    ui.wait(15, tag="turn_query", contains=question)
    card = subtree_text(ui.wait(timeout, tag="evidence_card"))
    stop = ui.find(tag="ask_stop")
    if stop is not None:
        ui.tap(stop)
    end = time.time() + 60
    while ui.find(tag="ask_stop") is not None and time.time() < end:
        time.sleep(1)
    row = ui.find(tag="sources_row")
    sources = subtree_text(row) if row is not None else ""
    r.shot(f"{label}-card", card.replace("\n", " ")[:160])
    return {"card": card, "sources": sources}


def fresh_app(r: Run, remove_docs: bool = True) -> None:
    if remove_docs:
        adb("shell", "run-as", PKG, "sh", "-c", "'rm -rf files/library/packs/doc-*'")
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
    r.ui.wait(90, tag="open_library")
    r.ui.wait(120, contains="Ready")


def pick_from_downloads(r: Run, filename: str) -> None:
    ui = r.ui
    for _ in range(3):
        try:
            ui.tap(ui.wait(10, text=filename))
            return
        except TimeoutError:
            ui.tap(ui.wait(15, text="Show roots"))
            time.sleep(1.5)
            ui.tap(ui.wait(10, text="Downloads"))
    raise TimeoutError(f"{filename} not shown in the picker")


def select_all(r: Run) -> None:
    """The picker is open on Downloads: long-press the first .tar, choose Select all, confirm."""
    ui = r.ui
    ui.wait(20, contains=".tar")
    x, y = ui.center(ui.find(contains=".tar"))
    adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "900")
    ui.tap(ui.wait(10, text="More options"))
    ui.tap(ui.wait(10, text="Select all"))
    time.sleep(1)
    r.shot("catalog-picker", "all files selected")
    ui.tap(ui.wait(10, text="Select"))


def tap_add_document(r: Run) -> None:
    """Scroll until the button is clear of the system bar and the floating button, then tap it."""
    ui = r.ui
    for _ in range(10):
        n = ui.find(tag="add_document")
        if n is not None and 300 < ui.center(n)[1] < 2080:
            ui.tap(n)
            return
        adb("shell", "input", "swipe", "540", "1700", "540", "900", "300")
        time.sleep(0.8)
    raise TimeoutError("Add document is not reachable")


def add_document(r: Run, filename: str, label: str) -> None:
    """Library is open: tap Add document, pick the file from Downloads, wait until indexing ends."""
    ui = r.ui
    tap_add_document(r)
    pick_from_downloads(r, filename)
    seen = counted = False
    t0 = time.time()
    end = t0 + 600
    while time.time() < end:
        n = ui.find(tag="import_progress")
        if n is None:
            if seen or time.time() - t0 > 20:
                break
        else:
            seen = True
            if not counted and "passages" in subtree_text(n):
                counted = True
                r.shot(f"{label}-indexing", subtree_text(n))
        time.sleep(0.5)
    else:
        raise TimeoutError("indexing did not finish")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="install the debug APK first")
    ap.add_argument("--push", nargs="*", default=[], help="pack ids to dev-push before starting")
    ap.add_argument("--question", default="Compare the Nile and the Amazon")
    ap.add_argument("--followup", default="Which one is longer?")
    ap.add_argument("--compare-with", default="the Mississippi River", help="typed after the Compare with… draft")
    ap.add_argument("--answer-timeout", type=float, default=240)
    ap.add_argument("--import-dir", help="dist dir of a split pack (packbuild split): test the SAF import with it")
    ap.add_argument("--catalog-dir", help="single-file packs (<id>.tar) plus the catalog.json built into the APK (see the catalog step): test Get more and Install from Downloads. With bundles in the catalog, the bundle step tests the first one.")
    ap.add_argument("--withhold", default="", help=".tar file of --catalog-dir that is missing at the first install and added for the second")
    ap.add_argument("--toggle-pack", default="stackexchange", help="installed pack id that the pack-switch step turns off and on")
    ap.add_argument("--toggle-question", default="Where to stay safe and how to get around when visiting Reykjavik", help="a question the toggled pack answers best")
    ap.add_argument("--only", nargs="*", default=[], help="run only these steps (pack-switch, my-pdf, my-txt, my-progress, my-errors, plus the names above)")
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
            doc = json.loads((d / "catalog.json").read_text())
            catalog = doc["packs"]
            bundle_files = {f["name"] for b in doc.get("bundles", []) for f in b["files"]}
            files = sorted(f.name for f in d.iterdir() if f.suffix == ".tar" and f.name not in bundle_files)
            ids = [f.removesuffix(".tar") for f in files]
            outside = [i for i in ids if i not in {p["pack_id"] for p in catalog}]
            adb("shell", "rm", "-rf", "/sdcard/Download/*")
            for f in files:
                if f != args.withhold:
                    adb("push", "-q", str(d / f), "/sdcard/Download/")
            if args.withhold:
                # The withheld file arrives cut short: the app names it and does not install the pack.
                (out / args.withhold).write_bytes((d / args.withhold).read_bytes()[: 1 << 20])
                adb("push", "-q", str(out / args.withhold), "/sdcard/Download/")
                (out / args.withhold).unlink()
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
            ui.wait(10, contains="Download the file below")
            buttons = [n for n in ui.nodes() if n.get("resource-id", "").endswith("download_file")]
            first = next(p for p in catalog if p["recommended"])
            if len(buttons) != 1 or len(first["files"]) != 1:
                raise RuntimeError(f"{len(buttons)} download buttons for {len(first['files'])} files")
            r.shot("catalog-sheet", f"{first['title']}: one Download button, {first['download_bytes']} bytes")
            ui.tap(buttons[0])
            time.sleep(2)
            r.shot("catalog-download", "Download opens the URL in a browser, or says there is none")
            r.back()
            if ui.find(tag="pack_sheet") is None:
                raise RuntimeError("the sheet is gone after the browser")
            ui.tap(ui.wait(10, tag="sheet_install"))

            select_all(r)
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
            if args.withhold and f"{args.withhold} (incomplete download)" not in note:
                raise RuntimeError(f"cut-short file not named: {note!r}")
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
                select_all(r)
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
                r.shot("catalog-complete", "the missing file was added and installed; notes.txt stays")

            # The last byte lies after the end of the tar archive, so the manifest checks pass. Only the catalog SHA-256 fails.
            name = catalog[0]["files"][0]["name"]
            data = bytearray((d / name).read_bytes())
            data[-1] ^= 1
            (out / name).write_bytes(data)
            adb("push", "-q", str(out / name), "/sdcard/Download/")
            (out / name).unlink()
            ui.tap(ui.wait(10, tag="import_pack"))
            select_all(r)
            note = " ".join(n.get("text", "") for n in ui.wait(180, tag="error_note").iter("node"))
            if "sha256 mismatch" not in note:
                raise RuntimeError(f"the changed {name} was not refused: {note!r}")
            r.shot("catalog-sha", note.replace("\n", " "))
            adb("shell", "rm", "-f", f"/sdcard/Download/{name}")
            ui.wait(10, tag="library_list")
            r.back()

    catalog_doc = json.loads((Path(args.catalog_dir) / "catalog.json").read_text()) if args.catalog_dir else {}
    if catalog_doc.get("bundles"):

        @step("bundle")
        def _():
            # The first bundle of the catalog: one tar file with several packs. A bundle over 100 MB skips the corrupted copies.
            d = Path(args.catalog_dir)
            b = catalog_doc["bundles"][0]
            ids, name = b["pack_ids"], b["files"][0]["name"]
            outside = next(p["pack_id"] for p in catalog_doc["packs"] if not any(p["pack_id"] in x["pack_ids"] for x in catalog_doc["bundles"]))
            small = b["download_bytes"] < 100e6

            def check(ok: bool, msg: str) -> None:
                if not ok:
                    raise RuntimeError(msg)

            def installed() -> list[str]:
                return adb("shell", "run-as", PKG, "ls", "files/library/packs").split()

            def restart() -> None:
                adb("shell", "am", "force-stop", PKG)
                adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")

            def reset(remove: list[str]) -> None:
                adb("shell", "rm", "-rf", "/sdcard/Download/*")
                for i in remove:
                    adb("shell", "run-as", PKG, "rm", "-rf", f"files/library/packs/{i}")
                restart()
                open_library(r)

            def push(src: Path) -> None:
                adb("push", "-q", str(src), "/sdcard/Download/")
                adb("shell", "sh", "-c", "'echo unrelated > /sdcard/Download/notes.txt'")

            def rows() -> list[tuple[int, str]]:
                return [(ui.center(n)[1], subtree_text(n)) for n in ui.nodes() if n.get("resource-id", "").endswith("available_row")]

            def note() -> str:
                return " ".join(n.get("text", "") for n in ui.wait(300, tag="error_note").iter("node"))

            def wait_import(label: str) -> list[str]:
                """Wait until the import ends. Returns the texts that the progress card showed."""
                seen: list[str] = []
                end = time.time() + 300
                while time.time() < end:
                    nodes = ui.nodes()
                    if any(n.get("text") == "Delete the downloaded files?" or n.get("resource-id", "").endswith("error_note") for n in nodes):
                        break
                    card = next((n for n in nodes if n.get("resource-id", "").endswith("import_progress")), None)
                    if card is not None and subtree_text(card) not in seen:
                        seen.append(subtree_text(card))
                        r.shot(f"{label}-progress", seen[-1])
                return seen

            def install(label: str) -> None:
                """Install from Downloads with every file selected."""
                ui.tap(ui.wait(10, tag="import_pack"))
                select_all(r)
                wait_import(label)

            def delete_sources() -> None:
                ui.wait(60, text="Delete the downloaded files?")
                r.shot("bundle-verified", "installed and verified")
                ui.tap(ui.wait(10, text="Delete"))
                ui.wait(10, contains="Deleted")
                check(sorted(adb("shell", "ls", "/sdcard/Download/").split()) == ["notes.txt"], "the unrelated file must stay in Downloads")

            def corrupt(label: str, offset: int, want: str, kept: list[str]) -> None:
                """A copy of the bundle with one flipped byte is refused. The packs that were complete before the damage stay."""
                reset(ids)
                data = bytearray((d / name).read_bytes())
                data[offset] ^= 1
                (out / name).write_bytes(data)
                push(out / name)
                (out / name).unlink()
                install(label)
                text = note()
                check(want in text and "mismatch" in text, f"unexpected refusal: {text!r}")
                have = installed()
                check([i for i in ids if i in have] == kept, f"installed {have}, expected {kept} of {ids}")
                ui.scroll_find(contains=f"{len(kept)} of {len(ids)} installed")
                r.shot(label, text.replace("\n", " "))

            # Get started on an empty library points to the one bundle file. The packs directory moves aside for a moment.
            adb("shell", "run-as", PKG, "mv", "files/library/packs", "files/library/packs.aside")
            adb("shell", "run-as", PKG, "mkdir", "files/library/packs")
            try:
                restart()
                card = subtree_text(ui.wait(90, tag="get_started"))
                size = f"{b['download_bytes'] / 1e9:.1f} GB" if b["download_bytes"] >= 1e9 else f"{b['download_bytes'] / 1e6:.0f} MB"
                check(b["title"] in card and "Download one file" in card and size in card, f"Get started card: {card!r}")
                r.shot("bundle-get-started", card)
                ui.tap(ui.scroll_find(tag="go_import"))
                ui.wait(10, tag="library_list")
            finally:
                adb("shell", "am", "force-stop", PKG)
                adb("shell", "run-as", PKG, "sh", "-c", "'rm -rf files/library/packs && mv files/library/packs.aside files/library/packs'")

            # Get more lists the bundle first, then the individual packs under their own label.
            reset(ids + [outside])
            ui.wait(10, tag="available_row")
            label_y = ui.center(ui.wait(10, text="Individual packs"))[1]
            above = [t for y, t in rows() if y < label_y]
            below = [t for y, t in rows() if y > label_y]
            check(above and b["title"] in above[0] and all("1 file" in t for t in above), f"bundle rows: {above!r}")
            check(below and not any("1 file" in t for t in below), f"pack rows: {below!r}")
            r.shot("bundle-available", above[0].replace(" | ", " · "))
            ui.tap(next(n for n in ui.nodes() if n.get("resource-id", "").endswith("available_row") and b["title"] in subtree_text(n)))
            ui.wait(10, tag="pack_sheet")
            listed = [n for n in ui.nodes() if n.get("resource-id", "").endswith("bundle_pack")]
            buttons = [n for n in ui.nodes() if n.get("resource-id", "").endswith("download_file")]
            check(len(listed) == len(ids) and len(buttons) == 1, f"{len(listed)} packs and {len(buttons)} Download buttons in the sheet")
            r.shot("bundle-sheet", f"{len(listed)} packs, one Download button")

            # One import installs every pack of the file. The unrelated file in Downloads is ignored.
            push(d / name)
            ui.tap(ui.wait(10, tag="sheet_install"))
            select_all(r)
            seen = wait_import("bundle")
            named =[t for t in seen if re.search(rf"Installing .+: \S+ \(\d+ of {len(ids)}\)", t)]
            check(small or named, f"the progress card never named a pack: {seen!r}")
            delete_sources()
            check(set(ids) <= set(installed()), f"installed {installed()}, expected {ids}")
            ui.wait(10, tag="library_list")
            check(not any(b["title"] in t for _, t in rows()), "the bundle row is still listed after its packs are installed")
            r.shot("bundle-installed", f"{ids} installed; progress texts seen: {len(seen)}, naming a pack: {len(named)}")

            # A single pack file still installs.
            reset([outside])
            push(d / f"{outside}.tar")
            install("single")
            delete_sources()
            check(outside in installed(), f"{outside} was not installed")
            r.shot("single-pack-installed", outside)

            if small:
                with tarfile.open(d / name) as tf:
                    big = max((m for m in tf.getmembers() if m.isfile() and m.name.startswith(ids[1] + "/")), key=lambda m: m.size)
                corrupt("bundle-corrupt-middle", big.offset_data + big.size // 2, ids[1], ids[:1])
                # The last byte lies after the end of the tar stream: only the SHA-256 of the whole file fails, so the last pack waits.
                corrupt("bundle-corrupt-last-byte", b["files"][0]["bytes"] - 1, "sha256", ids[:-1])
                reset(ids)
                push(d / name)
                install("bundle-again")
                delete_sources()
                check(set(ids) <= set(installed()), f"after the retry: {installed()}")
                r.shot("bundle-retry", "the good file installs after the refused copies")
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

    @step("pack-switch")
    def _():
        # A pack that is switched off is installed but not searched; its source must vanish and return.
        manifest = json.loads(adb("shell", "run-as", PKG, "cat", f"files/library/packs/{args.toggle_pack}/manifest.json"))
        title = manifest["title"]
        fresh_app(r, remove_docs=False)
        before = ask_card(r, args.toggle_question, "switch-on")
        if title not in before["card"]:
            raise RuntimeError(f"{title} is not the top source of {args.toggle_question!r}: {before['card']!r}")
        open_library(r)
        scroll_to_row(r, title)
        r.shot("switch-library-on", "every knowledge pack row has a switch")
        set_switch(r, title, on=False)
        r.shot("switch-library-off", f"{title}: dimmed, Off: not searched")
        r.back()
        try:
            off = ask_card(r, args.toggle_question, "switch-off")
            if title in off["card"] + off["sources"]:
                raise RuntimeError(f"{title} still answers while off: {off['card']!r}")
        finally:
            open_library(r)
            set_switch(r, title, on=True)
        r.shot("switch-library-back-on")
        r.back()
        after = ask_card(r, args.toggle_question, "switch-back-on")
        if title not in after["card"]:
            raise RuntimeError(f"{title} did not return after switching on: {after['card']!r}")

    def check_doc_source(res: dict, title: str, page: int) -> None:
        # The top card may be any page of the document; the page with the answer must be among the sources.
        if title not in res["card"] or not re.search(r"p\. \d", res["card"]) or f"p. {page}" not in res["card"] + res["sources"]:
            raise RuntimeError(f"expected {title!r}, a 'p. N' and 'p. {page}' in the card or sources: {res!r}")

    def doc_cycle(filename: str, title: str, label: str, page: int) -> None:
        """Add the file, find its passage by a question, switch it off, ask again, then remove it."""
        fresh_app(r)
        open_library(r)
        add_document(r, filename, label)
        scroll_to_row(r, title)
        r.shot(f"{label}-library", f"{title} listed under My documents")
        r.back()
        check_doc_source(ask_card(r, DOC_QUESTION, f"{label}-on"), title, page)
        open_library(r)
        set_switch(r, title, on=False)
        r.shot(f"{label}-library-off")
        r.back()
        off = ask_card(r, DOC_QUESTION, f"{label}-off")
        if title in off["card"] + off["sources"]:
            raise RuntimeError(f"{title} answers while off: {off['card']!r}")
        open_library(r)
        remove_row(r, title)
        ui.wait(15, contains=f"Removed {title}")
        time.sleep(1)
        if pack_row(ui, title) is not None:
            raise RuntimeError("the document is still listed after Remove")
        left = adb("shell", "run-as", PKG, "ls", "files/library/packs").split()
        if any(i.startswith("doc-") for i in left):
            raise RuntimeError(f"pack directory left behind: {left}")
        r.shot(f"{label}-removed")
        r.back()

    @step("my-pdf")
    def _():
        adb("shell", "rm", "-f", "/sdcard/Download/quillfeather.pdf")
        adb("push", "-q", str(FIXTURES / "quillfeather.pdf"), "/sdcard/Download/")
        if int(adb("shell", "getprop", "ro.build.version.sdk").strip()) < 35:
            print("  note: this device has no platform PDF text API unless SDK extension 13 is present")
        doc_cycle("quillfeather.pdf", "quillfeather", "pdf", page=2)

    @step("my-txt")
    def _():
        # A form feed starts a new page, so the keeper's paragraph is on page 2 as in the PDF.
        tmp = out / "harbour-notes.txt"
        tmp.write_text("\f".join(DOC_PAGES), encoding="utf-8")
        adb("shell", "rm", "-f", "/sdcard/Download/harbour-notes.txt")
        adb("push", "-q", str(tmp), "/sdcard/Download/")
        doc_cycle("harbour-notes.txt", "harbour-notes", "txt", page=2)

    @step("my-progress")
    def _():
        # A long file keeps the progress card on screen: "Indexing <title>…" and passages done of total.
        import random

        rng = random.Random(7)
        words = " ".join(DOC_PAGES).replace("\n", " ").split()
        pages = [" ".join(rng.choice(words) for _ in range(400)) for _ in range(150)]
        big = out / "long-notes.txt"
        big.write_text("\f".join(pages), encoding="utf-8")
        adb("push", "-q", str(big), "/sdcard/Download/")
        fresh_app(r)
        open_library(r)
        add_document(r, "long-notes.txt", "progress")
        if not any(s["step"] == "progress-indexing" for s in r.steps):
            raise RuntimeError("the progress card never showed passage counts")
        scroll_to_row(r, "long-notes")
        r.shot("progress-done", "the long document is listed with its passage count")
        remove_row(r, "long-notes")
        ui.wait(15, contains="Removed long-notes")
        r.back()

    @step("my-errors")
    def _():
        # The core's own message is shown, without the "msg=" wrapper: an empty file, then the same file twice.
        empty = out / "empty-note.txt"
        empty.write_text("  \n", encoding="utf-8")
        adb("push", "-q", str(empty), "/sdcard/Download/")
        tmp = out / "harbour-notes.txt"
        tmp.write_text("\f".join(DOC_PAGES), encoding="utf-8")
        adb("push", "-q", str(tmp), "/sdcard/Download/")
        fresh_app(r)
        open_library(r)
        tap_add_document(r)
        pick_from_downloads(r, "empty-note.txt")
        msg = ui.wait(120, contains="no text to search").get("text")
        r.shot("error-empty", msg)
        add_document(r, "harbour-notes.txt", "twice")
        tap_add_document(r)
        pick_from_downloads(r, "harbour-notes.txt")
        msg = ui.wait(120, contains="already in your library").get("text")
        if "msg=" in msg:
            raise RuntimeError(f"raw error text: {msg}")
        r.shot("error-duplicate", msg)
        remove_row(r, "harbour-notes")
        ui.wait(15, contains="Removed harbour-notes")
        r.back()

    def node_text(tag: str) -> str:
        n = ui.find(tag=tag)
        return (n.get("text") or n.get("content-desc") or "") if n is not None else ""

    @step("attribution")
    def _():
        # A Wikipedia-style answer: the card, the source cards, the passage page and the article page all name
        # the source and the license; the passage and article pages also show the page address as selectable text.
        fresh_app(r, remove_docs=False)
        res = ask_card(r, "Who invented the telephone?", "attr")
        credit = [n.get("text") for n in ui.nodes() if n.get("resource-id", "").endswith("source_credit_line")]
        if not credit or not all(" · " in c for c in credit):
            raise RuntimeError(f"source cards lack the pack and license line: {credit!r}")
        r.shot("attr-source-cards", " | ".join(credit[:3]))
        for _ in range(8):  # the answer streamed past the card: scroll back up to it
            if ui.find(tag="evidence_card") is not None:
                break
            adb("shell", "input", "swipe", "540", "600", "540", "1900", "200")
            time.sleep(0.6)
        ui.tap(ui.wait(10, tag="evidence_card"))
        ui.wait(10, tag="passage_view")
        ui.scroll_find(tag="source_credit")
        checks = {"source_attribution": "", "source_license": "License:", "source_url": "https://"}
        for tag, want in checks.items():
            if want not in node_text(tag):
                raise RuntimeError(f"passage page: {tag} is {node_text(tag)!r}, expected {want!r}")
        for tag in ("copy_url", "open_url"):
            if ui.find(tag=tag) is None:
                raise RuntimeError(f"passage page has no {tag}")
        r.shot("attr-passage", f"{node_text('source_license')} · {node_text('source_url')}")
        ui.tap(ui.scroll_find(tag="open_article"))
        ui.wait(10, tag="article_view")
        for _ in range(120):  # fling to the end of a long article
            if ui.find(tag="source_credit") is not None:
                break
            for _ in range(4):
                adb("shell", "input", "swipe", "540", "1900", "540", "300", "80")
        else:
            raise TimeoutError("the article page has no source credit at its end")
        if "https://" not in node_text("source_url") or "License:" not in node_text("source_license"):
            raise RuntimeError("article page lacks the license or the page address")
        r.shot("attr-article", node_text("source_url"))
        r.back()
        r.back()
        stop = ui.find(tag="ask_stop")
        if stop is not None:
            ui.tap(stop)

    @step("notice")
    def _():
        # "View notice" on a pack row shows that pack's NOTICE.txt (cdc-travel is small; the emulator's older packs predate notices).
        pack = "cdc-travel"
        manifest = json.loads((ROOT / f"data/library/packs/{pack}/manifest.json").read_text())
        notice = (ROOT / f"data/library/packs/{pack}/NOTICE.txt").read_text()
        subprocess.run([str(ROOT / "scripts/dev-push.sh"), pack], check=True)
        fresh_app(r, remove_docs=False)
        open_library(r)
        ui.tap(row_child(scroll_to_row(r, manifest["title"]), desc="Actions"))
        r.shot("notice-menu", "the pack row menu has View notice")
        ui.tap(ui.wait(5, text="View notice"))
        ui.wait(10, tag="notice_view")
        text = ui.all_text()
        first = notice.splitlines()[0]
        if first not in text:
            raise RuntimeError(f"notice text not shown: wanted {first!r}")
        r.shot("notice-shown", first)
        r.back()
        r.back()

    @step("licenses")
    def _():
        fresh_app(r, remove_docs=False)
        ui.tap(ui.wait(10, tag="overflow"))
        ui.tap(ui.wait(5, text="Settings"))
        ui.tap(ui.scroll_find(tag="open_licenses"))
        ui.wait(10, tag="licenses_view")
        if "llama.cpp" not in ui.all_text():
            raise RuntimeError("the license list does not show llama.cpp")
        r.shot("licenses-top", "llama.cpp listed")
        for _ in range(60):
            adb("shell", "input", "swipe", "540", "1900", "540", "300", "100")
        r.shot("licenses-scrolled", "scrolled well into the list")
        r.back()
        r.back()

    @step("ask-document")
    def _():
        adb("shell", "rm", "-f", "/sdcard/Download/quillfeather.pdf")
        adb("push", "-q", str(FIXTURES / "quillfeather.pdf"), "/sdcard/Download/")
        question = "When does the museum open?"
        fresh_app(r)
        open_library(r)
        add_document(r, "quillfeather.pdf", "scope")
        r.back()
        wide = ask_card(r, question, "scope-whole-library")
        r.shot("scope-whole-library-note", f"no scope: {wide['card'][:120]!r}")
        open_library(r)
        row = scroll_to_row(r, "quillfeather")
        ui.tap(row_child(row, tag="ask_document"))
        chip = ui.wait(10, tag="scope_chip")
        if "Searching: quillfeather" not in subtree_text(chip) and "Searching: quillfeather" not in (chip.get("text") or chip.get("content-desc") or ""):
            raise RuntimeError(f"scope chip text: {subtree_text(chip)!r}")
        ui.wait(5, tag="ask_input")
        r.shot("scope-chip", "back on Ask with the scope chip above the input")

        first = ask_card(r, question, "scope-q1")
        credit = [n.get("text") for n in ui.nodes() if n.get("resource-id", "").endswith("source_credit_line")]
        if "museum" not in first["card"] or not credit or not all("quillfeather" in c for c in credit):
            raise RuntimeError(f"a scoped question reached other packs: card {first['card']!r}, sources {credit!r}")
        if ui.find(tag="scope_chip") is None:
            raise RuntimeError("the chip vanished after a question")
        # A follow-up keeps the scope: ask on the same topic (no new topic) and check the sources again.
        ui.tap(ui.wait(10, tag="ask_input"))
        r.type_text("Who was the first keeper?")
        ui.tap(ui.wait(10, tag="ask_send"))
        ui.wait(15, tag="turn_query", contains="Who was the first keeper?")
        ui.wait(150, tag="sources_row")
        time.sleep(2)
        stop = ui.find(tag="ask_stop")
        if stop is not None:
            ui.tap(stop)
        for _ in range(60):
            if ui.find(tag="ask_stop") is None:
                break
            time.sleep(1)
        credit = [n.get("text") for n in ui.nodes() if n.get("resource-id", "").endswith("source_credit_line")]
        if not credit or not all("quillfeather" in c for c in credit) or ui.find(tag="scope_chip") is None:
            raise RuntimeError(f"the follow-up left the scope: {credit!r}")
        r.shot("scope-follow-up", "follow-up: chip still set, every source is the document")
        ui.tap(ui.wait(5, tag="scope_chip"))
        time.sleep(1)
        if ui.find(tag="scope_chip") is not None:
            raise RuntimeError("the chip stays after a tap on it")
        r.shot("scope-removed", "chip removed: questions search the whole library again")
        open_library(r)
        remove_row(r, "quillfeather")
        ui.wait(15, contains="Removed quillfeather")
        r.back()

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
