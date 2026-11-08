#!/usr/bin/env python3
"""End-to-end run of the Android app on a device or emulator, driven through adb + uiautomator.

It walks the main user flows, takes a screenshot at each step, and writes
artifacts/e2e/<stamp>/{NN-step.png, report.json, index.html} for a person to review.

Usage (inside `nix develop`, with the emulator running and the APK built):
  python3 scripts/e2e_emulator.py [--install] [--push simplewiki lfm25-1.2b] [--question "..."]
"""

from __future__ import annotations

import argparse
import contextlib
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
    files = [n for n in ui.dump().iter("node") if n.get("text", "").endswith(".tar")]
    x, y = ui.center(files[0])
    adb("shell", "input", "swipe", str(x), str(y), str(x), str(y), "900")
    # With one file, the long press already selects all, and the menu offers only "Deselect all".
    if len(files) > 1:
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
    ap.add_argument("--voice-tar", help="voice-en.tar (packbuild split --single): enables the voice step, which installs it from Downloads")
    ap.add_argument("--withhold", default="", help=".tar file of --catalog-dir that is missing at the first install and added for the second")
    ap.add_argument("--toggle-pack", default="stackexchange", help="installed pack id that the pack-switch step turns off and on")
    ap.add_argument("--toggle-question", default="Where to stay safe and how to get around when visiting Reykjavik", help="a question the toggled pack answers best")
    ap.add_argument("--failure-paths", metavar="TAR_DIR", help="also run the failure steps (fp-*). They kill the app and fill the disk, so use an emulator. TAR_DIR holds cdc-travel.tar and simplewiki.tar, and the app must start with only ling3-tiny and simplewiki")
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
                """A copy of the bundle with one flipped byte is refused, and `kept` is what stays installed."""
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
                if kept:
                    ui.scroll_find(contains=f"{len(kept)} of {len(ids)} installed")
                r.shot(label, text.replace("\n", " "))

            # Get started on an empty library points to the one bundle file. The packs directory moves aside for a moment.
            adb("shell", "run-as", PKG, "mv", "files/library/packs", "files/library/packs.aside")
            adb("shell", "run-as", PKG, "mkdir", "files/library/packs")
            try:
                restart()
                card = subtree_text(ui.wait(90, tag="get_started"))
                size = f"{b['download_bytes'] / 1e9:.1f} GB" if b["download_bytes"] >= 1e9 else f"{b['download_bytes'] / 1e6:.0f} MB"
                check(b["title"] in card and size in card, f"Get started card: {card!r}")
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
                # The hash of the whole file decides before anything moves into place, so a bad file installs nothing.
                corrupt("bundle-corrupt-middle", big.offset_data + big.size // 2, "sha256", [])
                # The last byte lies after the end of the tar stream: only the SHA-256 of the whole file fails.
                corrupt("bundle-corrupt-last-byte", b["files"][0]["bytes"] - 1, "sha256", [])
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
        ui.wait(15, tag="source_sheet")
        r.shot("source-sheet")
        ui.tap(ui.wait(15, tag="source_details"))
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
        ui.tap(ui.wait(10, tag="open_settings"))
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
        # The list opens at the last saved turn: the stop step's question, or "thanks!" if it stopped before a word.
        ui.wait(15, tag="turn_query")
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
        # A Wikipedia-style answer: the source cards name the pack; the passage and article pages name the source
        # and the license and show the page address as selectable text.
        fresh_app(r, remove_docs=False)
        res = ask_card(r, "Who invented the telephone?", "attr")
        credit = [n.get("text") for n in ui.nodes() if n.get("resource-id", "").endswith("source_credit_line")]
        if not credit or not all(credit):
            raise RuntimeError(f"source cards lack the pack line: {credit!r}")
        r.shot("attr-source-cards", " | ".join(credit[:3]))
        for _ in range(8):  # the answer streamed past the card: scroll back up to the question above it
            if ui.find(tag="turn_query") is not None:
                break
            adb("shell", "input", "swipe", "540", "600", "540", "1900", "200")
            time.sleep(0.6)
        ui.tap(ui.wait(10, tag="evidence_card"))
        if ui.find(tag="evidence_fold") is not None:  # the first tap unfolds a folded card, the second opens the source
            time.sleep(0.5)
            ui.tap(ui.wait(10, tag="evidence_card"))
        ui.tap(ui.wait(15, tag="source_details"))
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
        ui.tap(ui.wait(10, tag="open_settings"))
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

    if args.voice_tar:

        @step("voice")
        def _():
            # Install the voice pack from Downloads (no developer copy of the model), listen, stop, then remove the pack.
            adb("shell", "rm", "-rf", "/sdcard/Download/*")
            adb("push", "-q", args.voice_tar, "/sdcard/Download/voice-en.tar")
            adb("shell", "run-as", PKG, "sh", "-c", "'rm -rf files/stt files/library/packs/voice-en'")
            fresh_app(r, remove_docs=False)
            if ui.find(tag="mic_toggle") is not None:
                raise RuntimeError("the mic shows without a voice model")
            r.shot("voice-before", "no voice pack: no mic button")
            open_library(r)
            ui.tap(ui.wait(10, tag="import_pack"))
            pick_from_downloads(r, "voice-en.tar")
            ui.wait(300, text="Delete the downloaded files?")
            ui.tap(ui.wait(10, text="Delete"))
            text = subtree_text(scroll_to_row(r, "Voice input (Moonshine Medium, English)"))
            if "speak your question" not in text or "MIT" not in text or "loaded" in text:
                raise RuntimeError(f"voice row: {text!r}")
            r.shot("voice-library", text)
            r.back()
            ui.tap(ui.wait(10, tag="mic_toggle"))
            ui.wait(120, contains="Stop listening")
            time.sleep(3)
            r.shot("voice-listening", "mic on: the model loaded from the pack")
            ui.tap(ui.wait(10, tag="mic_toggle"))
            ui.wait(10, contains="Speak your question")
            if not adb("shell", "pidof", PKG, check=False).strip():
                raise RuntimeError("the app died after stop")
            r.shot("voice-stopped", "listening stopped")
            open_library(r)
            ui.tap(row_child(scroll_to_row(r, "Voice input (Moonshine Medium, English)"), desc="Actions"))
            ui.tap(ui.wait(5, text="Remove"))
            ui.wait(5, contains="Remove voice-en?")  # a model pack's dialog names the pack id
            ui.tap(ui.wait(5, text="Remove"))
            ui.wait(15, contains="Removed")
            r.back()
            ui.wait(10, tag="ask_input")
            time.sleep(1)
            if ui.find(tag="mic_toggle") is not None:
                raise RuntimeError("the mic stays after the pack was removed")
            r.shot("voice-removed", "pack removed: no mic button, no restart")

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
        if "quillfeather" not in first["card"] or not credit or not all("quillfeather" in c for c in credit):
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

    if args.failure_paths:
        tars = Path(args.failure_paths)
        adb("logcat", "-b", "crash", "-c", check=False)

        def must(ok: object, msg: str) -> None:
            if not ok:
                raise RuntimeError(msg)

        def pid() -> str:
            return adb("shell", "pidof", PKG, check=False).strip()

        def kill() -> None:
            adb("shell", "run-as", PKG, "kill", "-9", pid())

        def launch() -> None:
            """Android sometimes drops a start right after a force stop, so start again until the app has focus."""
            for _ in range(3):
                adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity")
                for _ in range(15):
                    focus = adb("shell", "dumpsys", "window").split("mCurrentFocus=", 1)[-1].split("\n", 1)[0]
                    if PKG in focus:
                        return
                    time.sleep(1)
            raise TimeoutError("the app did not come on screen")

        def relaunch() -> None:
            adb("shell", "am", "force-stop", PKG)
            launch()

        def hide() -> None:
            adb("shell", "input", "keyevent", "KEYCODE_HOME")
            time.sleep(2)

        def pss() -> int:
            return int(re.search(r"TOTAL PSS:\s+(\d+)", adb("shell", "dumpsys", "meminfo", PKG)).group(1))

        def last_saved(q: str, t0: int) -> dict | None:
            """The question's turn in the newest conversation file if the app saved it after t0 (ms). Only ended turns are saved."""
            name = adb("shell", "run-as", PKG, "sh", "-c", "'ls -t files/conversations | head -1'").strip()
            if not name:
                return None
            doc = json.loads(adb("exec-out", "run-as", PKG, "cat", f"files/conversations/{name}"))
            ok = doc["updated"] >= t0 and doc["turns"] and doc["turns"][-1]["query"] == q
            return doc["turns"][-1] if ok else None

        def saved_turn(q: str, t0: int) -> dict:
            end = time.time() + 300
            while (turn := last_saved(q, t0)) is None:
                must(time.time() < end, "the answer did not end while the app was hidden")
                time.sleep(3)
            return turn

        def to_ask_screen() -> None:
            for _ in range(5):
                if ui.find(tag="open_settings") is not None:
                    return
                r.back()
            raise TimeoutError("not back on the Ask screen")

        def ready(timeout: float = 300) -> None:
            ui.wait(timeout, tag="model_status", contains="Ready")

        def import_downloads() -> None:
            ui.tap(ui.wait(30, tag="import_pack"))
            select_all(r)

        def downloads(*files: Path) -> None:
            adb("shell", "rm", "-rf", "/sdcard/Download/*")
            for f in files:
                adb("push", "-q", str(f), "/sdcard/Download/")

        def error_note() -> str:
            return " ".join(n.get("text", "") for n in ui.wait(120, tag="error_note").iter("node"))

        @step("fp-low-ram-choice")
        def _():
            # The emulator has 8 GB, so it counts as a low-RAM phone, and the app does not load Ling by itself.
            relaunch()
            ui.wait(60, tag="model_status", contains="small model")
            r.shot("fp-low-ram-status", ui.find(tag="model_status").get("text"))
            ui.tap(ui.wait(10, tag="open_settings"))
            row = ui.wait(10, tag="model_fast")
            must("Android may stop the app" in subtree_text(row), f"no warning: {subtree_text(row)!r}")
            r.shot("fp-low-ram-picker", "one model, and the user can still choose it")
            ui.tap(row)
            to_ask_screen()
            ready()
            r.shot("fp-low-ram-loaded", "Ling loaded after the user chose it")

        @step("fp-double-back")
        def _():
            ui.tap(ui.wait(60, tag="open_library"))
            ui.wait(10, tag="library_list")
            # The second tap must land during the page animation, which the emulator script switches off.
            adb("shell", "settings", "put", "global", "animator_duration_scale", "1")
            try:
                x, y = ui.center(ui.wait(5, text="Back"))
                adb("shell", f"input tap {x} {y} & input tap {x} {y} & wait")
                time.sleep(2)
                r.shot("fp-double-back", "two taps on Back during the page animation")
                must(ui.find(tag="open_settings") is not None, "the Ask screen is gone after a double Back")
            finally:
                adb("shell", "settings", "put", "global", "animator_duration_scale", "0")

        @step("fp-too-long")
        def _():
            ui.tap(ui.wait(20, tag="ask_input"))
            r.type_text("a" * 4100)
            ui.wait(20, tag="too_long")
            send = ui.wait(5, tag="ask_send")
            must(send.get("enabled") == "false", "the send button stays on for a question over the limit")
            r.shot("fp-too-long", "the box says why, and send is off")
            # Ctrl+A does not always reach the box; Delete then removes one letter, so repeat until the text is gone.
            for _ in range(3):
                adb("shell", "input", "keycombination", "113", "29")
                adb("shell", "input", "keyevent", "KEYCODE_DEL")
                time.sleep(1)
                if ui.find(tag="ask_input", contains="aaa") is None:
                    break
            must(ui.find(tag="ask_input", contains="aaa") is None, "the text did not clear")
            must(ui.find(tag="too_long") is None, "the note stays after the text is gone")

        @step("fp-restore-after-kill")
        def _():
            results["questions"].append(ask_and_wait(r, "What is the capital of France?", "fp-restore", args.answer_timeout))
            ui.tap(ui.wait(10, tag="ask_input"))
            r.type_text("my unsent draft")
            time.sleep(1)
            hide()
            kill()
            time.sleep(2)
            must(not pid(), "the process is still alive")
            launch()
            ui.wait(60, tag="turn_query", contains="capital of France")
            must(ui.wait(10, tag="ask_input", contains="my unsent draft") is not None, "the draft is gone")
            r.shot("fp-restored", "the conversation and the draft came back")
            adb("shell", "input", "keycombination", "113", "29")
            adb("shell", "input", "keyevent", "KEYCODE_DEL")
            # A question without an answer yet comes back as a draft.
            ready()
            ui.tap(ui.wait(10, tag="ask_input"))
            r.type_text("Tell me about the river Danube")
            ui.tap(ui.wait(10, tag="ask_send"))
            ui.wait(20, tag="ask_stop")
            hide()
            kill()
            time.sleep(2)
            launch()
            ui.wait(60, tag="ask_input", contains="Tell me about the river Danube")
            r.shot("fp-restored-question", "the unanswered question waits in the box")
            adb("shell", "input", "keycombination", "113", "29")
            adb("shell", "input", "keyevent", "KEYCODE_DEL")

        @step("fp-moved-source")
        def _():
            ready()
            def first_source() -> None:
                row = ui.scroll_find(tag="sources_row")
                ui.tap(next(n for n in row.iter("node") if n is not row and n.get("clickable") == "true"))
                ui.wait(15, tag="source_sheet")
            first_source()
            must(ui.find(tag="source_moved") is None, "a source that did not change shows as moved")
            must(ui.find(tag="source_details") is not None, "no details for a source that did not change")
            r.shot("fp-source-same", "the same pack: the passage opens as usual")
            ui.tap(ui.wait(5, tag="sheet_close"))
            time.sleep(1)
            name = adb("shell", "run-as", PKG, "sh", "-c", "'ls -t files/conversations | head -1'").strip()
            doc = json.loads(adb("exec-out", "run-as", PKG, "cat", f"files/conversations/{name}"))
            for s in doc["turns"][-1]["sources"]:
                s["title"] = "An article of an older pack build"
            hide()
            subprocess.run(["adb", "exec-in", "run-as", PKG, "sh", "-c", f"cat > files/conversations/{name}"], input=json.dumps(doc), text=True, check=True)
            relaunch()
            ui.tap(ui.wait(60, tag="open_history"))
            ui.tap(next(n for n in ui.wait(10, tag="history_list").iter("node") if n.get("clickable") == "true"))
            ui.wait(15, tag="turn_query")
            first_source()
            ui.wait(10, tag="source_moved")
            must(ui.find(tag="source_details") is None, "the sheet offers the wrong passage")
            r.shot("fp-source-moved", "a saved number that now holds other text: the saved start and a note, not the wrong article")
            ui.tap(ui.wait(5, tag="sheet_close"))

        @step("fp-hide-during-answer")
        def _():
            # Ling unloads when the app goes off screen; an answer that runs then keeps it until the answer ends.
            # One thread and a large evidence budget give a long prefill, so the app hides while the answer runs.
            with settings('<int name="threads" value="1" /><int name="evidence_override" value="2500" />'):
                new_topic(r)
                q = "Describe in detail how castles were built in the Middle Ages"
                t0 = int(time.time() * 1000)
                ui.tap(ui.wait(20, tag="ask_input"))
                r.type_text(q)
                ui.tap(ui.wait(10, tag="ask_send"))
                ui.wait(60, tag="evidence_card")
                hide()
                during = pss()
                must(last_saved(q, t0) is None, "the answer ended before the app hid: the test needs a longer prefill")
                must(during > 2_000_000, f"Ling is not loaded during the answer: {during} kB")
                turn = saved_turn(q, t0)
                must(not turn["stopped"] and not turn["error"] and turn["segments"], f"the hidden answer was cut: {turn}")
                time.sleep(5)
                after = pss()
                must(after < during - 1_000_000, f"Ling stayed loaded after the answer: {during} kB -> {after} kB")
                launch()
                ui.wait(20, tag="turn_query", contains=q)
                r.shot("fp-hide-during-answer", f"the answer ended off screen, then Ling unloaded: {during // 1024} MB -> {after // 1024} MB")

        @step("fp-notification-unload")
        def _():
            # Unload in the notification frees the memory at once: it stops a running answer, then the model goes.
            # One thread and a large evidence budget give a long prefill, so the tap lands while the answer runs.
            with settings('<int name="threads" value="1" /><int name="evidence_override" value="2500" />'):
                new_topic(r)
                q = "Describe in detail how bridges were built in Roman times"
                t0 = int(time.time() * 1000)
                ui.tap(ui.wait(20, tag="ask_input"))
                r.type_text(q)
                ui.tap(ui.wait(10, tag="ask_send"))
                ui.wait(60, tag="evidence_card")
                hide()
                during = pss()
                must(during > 2_000_000, f"Ling is not loaded during the answer: {during} kB")
                adb("shell", "cmd", "statusbar", "expand-notifications")
                end = time.time() + 15
                unload = None
                while unload is None and time.time() < end:
                    unload = next((n for n in ui.nodes() if (n.get("text") or "").lower() == "unload"), None)
                must(unload is not None, "no Unload action in the notification shade")
                r.shot("fp-notification-unload-shade", "the notification offers Unload during the answer")
                must(last_saved(q, t0) is None, "the answer ended before the Unload tap: the test needs a longer prefill")
                tapped = time.time()
                ui.tap(unload)
                adb("shell", "cmd", "statusbar", "collapse")
                turn = saved_turn(q, t0)
                ended = time.time() - tapped
                must(turn["stopped"], f"the answer ran on after Unload: {turn}")
                time.sleep(3)
                after = pss()
                must(after < during - 1_000_000, f"the model stayed loaded after Unload: {during} kB -> {after} kB")
                time.sleep(35)
                services = adb("shell", "dumpsys", "activity", "services", PKG)
                must("InferenceService" not in services, "the service still runs 35 s after Unload")
                launch()
                ui.wait(20, tag="turn_query", contains=q)
                r.shot("fp-notification-unload", f"answer stopped {ended:.0f} s after Unload; {during // 1024} -> {after // 1024} MB; service gone")

        @step("fp-model-change-stops-answer")
        def _():
            ready()
            # A second copy of Ling as the small model gives Settings a second choice.
            adb("shell", "am", "force-stop", PKG)
            adb("shell", "run-as", PKG, "sh", "-c", "'cd files/library/packs && rm -rf ling-small && cp -r ling3-tiny ling-small && sed -i -e s/ling3-tiny/ling-small/ -e s/llm-fast/llm-small/ ling-small/manifest.json'")
            launch()
            ready()
            new_topic(r)
            ui.tap(ui.wait(20, tag="ask_input"))
            r.type_text("Explain in detail how the Roman Empire fell")
            ui.tap(ui.wait(10, tag="ask_send"))
            ui.wait(20, tag="ask_stop")
            ui.tap(ui.wait(10, tag="open_settings"))
            ui.tap(ui.wait(10, tag="model_small"))
            to_ask_screen()
            ui.wait(120, text="Stopped")
            r.shot("fp-model-change", "the cut-off answer says Stopped, not done")
            ready()

        @step("fp-crash-mark")
        def _():
            relaunch()
            ui.wait(60, tag="model_status", contains="Loading")
            adb("shell", "am", "force-stop", PKG)
            launch()
            ready()
            r.shot("fp-force-stop-during-load", "the user closed the app during a load: the model still loads")
            relaunch()
            ui.wait(60, tag="model_status", contains="Loading")
            kill()
            time.sleep(2)
            if not pid():
                launch()
            ui.wait(60, tag="model_status", contains="failed")
            ui.tap(ui.wait(10, tag="open_settings"))
            ui.wait(10, tag="lite_crashed")
            r.shot("fp-crash-note", subtree_text(ui.find(tag="lite_crashed")))
            ui.tap(ui.wait(10, tag="model_small"))
            to_ask_screen()
            ready()
            r.shot("fp-crash-retry", "choosing the model tries it again")
            # The chosen model crashes and another one is installed: the other one answers, and the status names it.
            ui.tap(ui.wait(10, tag="open_settings"))
            ui.tap(ui.wait(10, tag="model_fast"))
            to_ask_screen()
            ready()
            relaunch()
            ui.wait(60, tag="model_status", contains="Loading")
            kill()
            time.sleep(2)
            if not pid():
                launch()
            status = ui.wait(120, tag="model_status", contains="instead").get("text")
            r.shot("fp-crash-fallback", status)
            ui.tap(ui.wait(10, tag="open_settings"))
            ui.tap(ui.wait(10, tag="model_small"))
            to_ask_screen()
            ready()
            must("instead" not in ui.find(tag="model_status").get("text"), "the status still names a fallback after the user chose a model")

        @step("fp-broken-pack")
        def _():
            # A pack with a damaged manifest does not load; the user can still remove it and get its storage back.
            adb("shell", "am", "force-stop", PKG)
            adb("shell", "run-as", PKG, "sh", "-c", "'mkdir -p files/library/packs/broken-pack && echo { > files/library/packs/broken-pack/manifest.json && dd if=/dev/zero of=files/library/packs/broken-pack/data.bin bs=1048576 count=50 2>/dev/null'")
            launch()
            open_library(r)
            # The section is last; tap the button only once it is clear of the floating Install button.
            for _ in range(20):
                n = ui.find(tag="remove_skipped")
                if n is not None and ui.center(n)[1] < 2000:
                    r.shot("fp-broken-pack-listed", "the pack that does not load, with its reason and a Remove button")
                    ui.tap(n)
                    break
                adb("shell", "input", "swipe", "540", "1700", "540", "900", "300")
                time.sleep(0.8)
            else:
                raise TimeoutError("the Remove button of the pack that does not load is not reachable")
            ui.wait(5, contains="Remove broken-pack?")
            ui.tap(ui.wait(5, text="Remove"))
            ui.wait(15, contains="Removed broken-pack")
            left = adb("shell", "run-as", PKG, "ls", "-a", "files/library/packs")
            must("broken-pack" not in left, f"the pack is still on disk: {left}")
            r.shot("fp-broken-pack", "a pack that does not load is removed, and its storage is free")
            to_ask_screen()

        @step("fp-sideload")
        def _():
            tar = tars / "cdc-travel.tar"
            adb("shell", "run-as", PKG, "rm", "-rf", "files/library/packs/cdc-travel")
            relaunch()
            open_library(r)
            downloads(tar)
            import_downloads()
            ui.tap(ui.wait(120, text="Delete"))
            ui.wait(10, contains="Deleted")
            row = scroll_to_row(r, "CDC")
            must("not from the app's catalog" in subtree_text(row), f"no label: {subtree_text(row)!r}")
            r.shot("fp-sideload-label", "a file outside the catalog installs with a label")
            downloads(tar)
            import_downloads()
            note = error_note()
            must("is installed" in note, f"the file replaced the pack: {note!r}")
            r.shot("fp-sideload-replace", note)
            remove_row(r, "CDC travel health by destination")
            ui.wait(15, contains="Removed")
            renamed = Path(args.failure_paths) / "notes.tar"
            renamed.write_bytes(tar.read_bytes())
            downloads(renamed)
            renamed.unlink()
            import_downloads()
            note = error_note()
            must("holds the pack cdc-travel" in note, f"a wrong name passed: {note!r}")
            must("cdc-travel" not in adb("shell", "run-as", PKG, "ls", "files/library/packs"), "the pack installed under another file name")
            r.shot("fp-sideload-name", note)
            adb("shell", "rm", "-rf", "/sdcard/Download/*")
            to_ask_screen()

        @step("fp-kill-during-install")
        def _():
            adb("shell", "am", "force-stop", PKG)
            # Kept aside, so a failed step does not leave the later steps without a library.
            adb("shell", "run-as", PKG, "mv", "files/library/packs/simplewiki", "files/simplewiki-kept")
            try:
                launch()
                # A kill during a model load counts against the model; this step tests only the install.
                ready()
                open_library(r)
                downloads(tars / "simplewiki.tar")
                import_downloads()
                end = time.time() + 60
                while "0" != adb("shell", "run-as", PKG, "sh", "-c", "'test -d files/library/packs/.staging; echo $?'").strip():
                    must(time.time() < end, "the install never started")
                services = adb("shell", "dumpsys", "activity", "services", PKG)
                must("InferenceService" in services and "isForeground=true" in services, "no foreground service protects the install")
                kill()
                time.sleep(2)
                if not pid():
                    launch()
                open_library(r)
                note = error_note()
                must("before the install ended" in note, f"no note: {note!r}")
                left = adb("shell", "run-as", PKG, "ls", "-a", "files/library/packs")
                must(".staging" not in left, f"partial files stayed: {left}")
                r.shot("fp-install-killed", note)
                import_downloads()
                ui.tap(ui.wait(180, text="Delete"))
                ui.wait(10, contains="Deleted")
                must("simplewiki" in adb("shell", "run-as", PKG, "ls", "files/library/packs"), "the second try did not install")
                r.shot("fp-install-retried", "the same file installs after the kill")
                to_ask_screen()
            finally:
                if "simplewiki" not in adb("shell", "run-as", PKG, "ls", "files/library/packs"):
                    adb("shell", "am", "force-stop", PKG)
                    adb("shell", "run-as", PKG, "mv", "files/simplewiki-kept", "files/library/packs/simplewiki")
                    launch()
                adb("shell", "run-as", PKG, "rm", "-rf", "files/simplewiki-kept")

        @contextlib.contextmanager
        def settings(extra: str):
            """Restart the app with extra settings, then put the old ones back. A later entry for a key wins."""
            ready()
            hide()
            path = "shared_prefs/commonplace.xml"
            xml = adb("exec-out", "run-as", PKG, "cat", path)
            put = lambda s: subprocess.run(["adb", "exec-in", "run-as", PKG, "sh", "-c", f"cat > {path}"], input=s, text=True, check=True)
            adb("shell", "am", "force-stop", PKG)
            put(xml.replace("</map>", extra + "</map>"))
            try:
                launch()
                ready()
                yield
            finally:
                adb("shell", "am", "force-stop", PKG)
                put(xml)
                launch()

        @step("fp-big-settings")
        def _():
            # A large evidence budget, a long answer and a long reasoning budget do not fit the 4,096-token context of Ling together.
            with settings('<int name="evidence_override" value="2500" /><int name="answer_tokens" value="700" /><int name="think_budget" value="1536" /><boolean name="thinking" value="true" />'):
                ui.tap(ui.wait(20, tag="ask_input"))
                r.type_text("Explain how the printing press changed Europe")
                ui.tap(ui.wait(10, tag="ask_send"))
                ui.wait(20, tag="ask_stop")
                time.sleep(25)
                note = ui.find(tag="error_note")
                must(note is None, f"the question failed: {subtree_text(note) if note is not None else ''}")
                r.shot("fp-big-settings", "the settings do not fit, and the question still runs")
                if (stop := ui.find(tag="ask_stop")) is not None:
                    ui.tap(stop)
                ui.wait(180, tag="ask_send")

        @step("fp-stop-during-prefill")
        def _():
            # One thread and a large evidence budget make the emulator's prefill long enough to tap Stop inside it.
            with settings('<int name="threads" value="1" /><int name="evidence_override" value="2500" />'):
                new_topic(r)
                times = []
                for q in ["How do volcanoes form and why do they erupt", "Why did the Soviet Union collapse"]:
                    ui.tap(ui.wait(20, tag="ask_input"))
                    r.type_text(q)
                    ui.tap(ui.wait(10, tag="ask_send"))
                    # The card appears when the search ends; the prefill starts right after it.
                    ui.wait(60, tag="evidence_card")
                    stop = ui.wait(10, tag="ask_stop")
                    t0 = time.time()
                    ui.tap(stop)
                    ui.wait(180, tag="ask_send")
                    times.append((round(time.time() - t0, 1), "no answer text" if ui.find(tag="answer_text") is None else "some answer text"))
                    ui.wait(5, text="Stopped")
                    ui.tap(ui.wait(20, tag="new_topic"))
                results["stop_during_prefill_s"] = times
                r.shot("fp-stop-during-prefill", f"seconds from the Stop tap to the send button: {times}")

        @step("fp-full-disk")
        def _():
            ready()
            downloads(tars / "cdc-travel.tar")
            def fill(name: str) -> None:
                avail = int(adb("shell", "df", "-k", "/data").splitlines()[-1].split()[3])
                if avail > 2000:
                    adb("shell", "run-as", PKG, "fallocate", "-l", f"{avail - 2000}k", f"files/{name}")
                adb("shell", "run-as", PKG, "sh", "-c", f"'dd if=/dev/zero of=files/{name}-rest bs=4k 2>/dev/null; true'")
            try:
                fill("fill")
                r.shot("fp-disk-full", adb("shell", "df", "-k", "/data").splitlines()[-1])
                ui.tap(ui.wait(20, tag="ask_input"))
                r.type_text("Explain in detail how the Roman Empire fell")
                ui.tap(ui.wait(10, tag="ask_send"))
                # The failed prompt-state save frees its partial file before the answer streams; fill that space too.
                ui.wait(120, tag="answer_text")
                fill("fill2")
                must(ui.find(tag="ask_stop") is not None, "the answer ended before the disk was full again, so the step proves nothing")
                ui.wait(180, contains="Could not save")
                must(pid(), "the app died on a full disk")
                r.shot("fp-save-failed", "the answer stays on screen, and the app says that it could not save")
                # The system file picker crashes on a disk with no room at all (SQLiteFullException), so leave it about 2 MB.
                adb("shell", "run-as", PKG, "rm", "-f", "files/fill-rest", "files/fill2", "files/fill2-rest")
                open_library(r)
                import_downloads()
                note = error_note()
                must("free storage" in note, f"unexpected note: {note!r}")
                r.shot("fp-install-no-space", note)
                to_ask_screen()
            finally:
                adb("shell", "run-as", PKG, "rm", "-f", "files/fill", "files/fill-rest", "files/fill2", "files/fill2-rest")
                adb("shell", "rm", "-rf", "/sdcard/Download/*")
            must(pid(), "the app died on a full disk")

        flows[:] = [f for f in flows if f[0].startswith("fp-")] if not args.only else flows

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
