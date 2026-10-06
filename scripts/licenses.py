#!/usr/bin/env python3
"""Write android/app/src/main/assets/third_party_licenses.txt: the open-source licenses of the app.

Run inside `nix develop` (needs cargo and the cargo registry sources on disk):  python3 scripts/licenses.py
Sources: cargo metadata of commonplace-ffi (both Android targets), the dependencies in
android/app/build.gradle.kts, native code, bundled models, and the Literata font. License texts are copied
from files on disk: third_party/, the repository LICENSE and the crate sources.
"""
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "android/app/src/main/assets/third_party_licenses.txt")
TARGETS = ["aarch64-linux-android", "x86_64-linux-android"]
# When a crate offers a choice (OR), the app uses the first of these that it offers.
PREFERENCE = ["MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "Zlib", "ISC", "MPL-2.0", "Unicode-3.0", "CC0-1.0", "Unlicense", "BSL-1.0"]
NOTICE_NAMES = ("NOTICE",)
LICENSE_PREFIXES = ("LICENSE", "LICENCE", "COPYING", "UNLICENSE")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r\n", "\n")


def elect(expr):
    """The licenses that apply to a crate: every AND part, with one pick from each OR choice."""
    if not expr:
        return []
    expr = expr.replace("/", " OR ")
    out = []
    for part in re.split(r"\s+AND\s+", expr):
        options = [o.strip(" ()") for o in re.split(r"\s+OR\s+", part)]
        pick = next((p for p in PREFERENCE if p in options), options[0])
        out.append(pick)
    return out


def rust_crates():
    seen = {}
    for target in TARGETS:
        meta = json.loads(subprocess.check_output(
            ["cargo", "metadata", "--format-version", "1", "--locked", "--offline", "--manifest-path", os.path.join(ROOT, "core/Cargo.toml"), "--filter-platform", target],
            text=True))
        pk = {p["id"]: p for p in meta["packages"]}
        nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
        root = next(i for i, p in pk.items() if p["name"] == "commonplace-ffi")
        stack, done = [root], set()
        while stack:
            i = stack.pop()
            if i in done:
                continue
            done.add(i)
            # Normal dependencies only: build tools and dev tools are not in the app.
            stack += [d["pkg"] for d in nodes[i]["deps"] if any(k["kind"] is None for k in d["dep_kinds"])]
        for i in done:
            p = pk[i]
            if p["source"]:  # workspace crates have no source: they are the app itself
                seen[(p["name"], p["version"])] = p
    return [seen[k] for k in sorted(seen)]


def license_files(crate_dir):
    return sorted(f for f in os.listdir(crate_dir) if f.upper().startswith(LICENSE_PREFIXES) and os.path.isfile(os.path.join(crate_dir, f)))


COPYRIGHT = re.compile(r"^\s*(Copyright\b.*(\d{4}|\(c\)|©)|\(c\)\s*\d|©)")
PLACEHOLDER = re.compile(r"yyyy|owner|holder|\[name|\{name|<year>|<name", re.I)


def copyright_lines(crate_dir):
    lines = []
    for f in license_files(crate_dir):
        text = read(os.path.join(crate_dir, f))
        if "Apache License" in text:  # its text only describes copyright notices
            continue
        for line in text.split("\n"):
            line = line.strip()
            if COPYRIGHT.match(line) and not PLACEHOLDER.search(line) and line not in lines:
                lines.append(line)
    return lines[:4]


def crate_section(crates):
    out = []
    notices = []
    for p in crates:
        d = os.path.dirname(p["manifest_path"])
        picked = elect(p["license"])
        head = f'{p["name"]} {p["version"]}  {p["license"] or "no license field"}'
        if p["license"] and " OR " in p["license"].replace("/", " OR "):
            head += f'  (used under {" and ".join(picked)})'
        out.append(head)
        if p.get("repository"):
            out.append("    " + p["repository"])
        lines = copyright_lines(d)
        if not lines and p.get("authors"):
            lines = ["Authors: " + ", ".join(p["authors"])]
        out += ["    " + l for l in lines]
        for f in sorted(os.listdir(d)):
            if f.upper().startswith(NOTICE_NAMES) and os.path.isfile(os.path.join(d, f)):
                notices.append((f'{p["name"]} {p["version"]}', read(os.path.join(d, f)).strip()))
    return out, notices


def gradle_deps():
    text = read(os.path.join(ROOT, "android/app/build.gradle.kts"))
    deps = []
    for m in re.finditer(r'"([\w.\-]+):([\w.\-]+)(?::([\w.\-+]+))?(?:@aar)?"', text):
        g, a, v = m.groups()
        if g in ("org.jetbrains.kotlin.plugin.compose",):
            continue
        deps.append((g, a, v))
    return sorted(set(deps))


ANDROID_LICENSE = {  # checked against each artifact's POM
    "androidx.": ("Apache-2.0", "The Android Open Source Project"),
    "com.google.ai.edge.litertlm": ("Apache-2.0", "Google LLC (https://github.com/google-ai-edge/LiteRT-LM)"),
    "com.microsoft.onnxruntime": ("MIT", "Microsoft Corporation (https://github.com/microsoft/onnxruntime)"),
    # The POM offers LGPL-2.1-or-later or Apache-2.0 at the user's choice.
    "net.java.dev.jna": ("Apache-2.0", "JNA contributors (https://github.com/java-native-access/jna); also offered under LGPL-2.1-or-later"),
    "ai.moonshine": ("MIT", "Moonshine AI (https://github.com/moonshine-ai/moonshine)"),
}


def android_section():
    out = []
    for g, a, v in gradle_deps():
        lic = next((val for k, val in ANDROID_LICENSE.items() if g.startswith(k)), None)
        if lic is None:
            sys.exit(f"no license recorded for {g}:{a}; read its POM and add it to ANDROID_LICENSE")
        ver = v or "version from the Compose BOM"
        out += [f"{g}:{a} {ver}  {lic[0]}", f"    {lic[1]}"]
    out += [
        "Their own dependencies (AndroidX, the Kotlin standard library, Kotlin coroutines) are Apache-2.0.",
        "The Moonshine library bundles its own ONNX Runtime 1.23.2 (MIT, Microsoft Corporation).",
    ]
    return out


def body_after_copyright(text):
    """The license text without its copyright line: the shared body of an MIT, BSD or ISC license."""
    if "Redistribution and use in source and binary forms" in text:  # BSD: drop any preamble and copyright line
        return text[text.index("Redistribution and use in source and binary forms"):].strip()
    lines = text.strip().split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or COPYRIGHT.match(lines[i]) or lines[i].strip().upper().endswith("LICENSE") and i < 2):
        i += 1
    return "\n".join(lines[i:]).strip()


def find(crates, predicate):
    for p in crates:
        d = os.path.dirname(p["manifest_path"])
        for f in license_files(d):
            t = read(os.path.join(d, f))
            if predicate(f, t):
                return os.path.join(d, f)
    # A crate without its own license file (uniffi): use the same text from another crate in the registry.
    for path in sorted(glob.glob(os.path.expanduser("~/.cargo/registry/src/*/*/LICEN*")) + glob.glob(os.path.expanduser("~/.cargo/registry/src/*/*/COPYING*"))):
        if os.path.isfile(path) and predicate(os.path.basename(path), read(path)):
            return path
    return None


def license_texts(crates, used):
    texts = {}
    sources = {}

    def put(name, path, strip=False):
        t = read(path)
        texts[name] = body_after_copyright(t) if strip else t.strip()
        sources[name] = os.path.relpath(path, ROOT) if path.startswith(ROOT) else path.split("/registry/src/")[-1]

    put("MIT", os.path.join(ROOT, "third_party/llama.cpp/LICENSE"), strip=True)
    put("Apache-2.0", os.path.join(ROOT, "LICENSE"))
    put("OFL-1.1", os.path.join(ROOT, "third_party/literata/OFL.txt"))
    finders = {
        "MPL-2.0": lambda f, t: "Mozilla Public License Version 2.0" in t and "Exhibit B" in t,
        "BSD-3-Clause": lambda f, t: "BSD" in f.upper() and "Neither the name" in t and "Redistribution and use in source and binary forms" in t,
        "BSD-2-Clause": lambda f, t: "Redistribution and use in source and binary forms" in t and "Neither the name" not in t,
        "ISC": lambda f, t: "Permission to use, copy, modify, and/or distribute this software for any purpose" in t,
        "Zlib": lambda f, t: "This software is provided 'as-is'" in t and "zlib" in t.lower() + " " or "Permission is granted to anyone to use this software for any purpose" in t,
        "Unicode-3.0": lambda f, t: "UNICODE LICENSE V3" in t,
        "CC0-1.0": lambda f, t: "CC0 1.0 Universal" in t,
        "Unlicense": lambda f, t: "This is free and unencumbered software" in t,
        "BSL-1.0": lambda f, t: "Boost Software License" in t,
    }
    for name in sorted(used):
        if name in texts:
            continue
        path = find(crates, finders[name]) if name in finders else None
        if path is None:
            sys.exit(f"no license text on disk for {name}")
        put(name, path, strip=name in ("BSD-3-Clause", "BSD-2-Clause", "ISC", "Zlib"))
    return texts, sources


def main():
    crates = rust_crates()
    crate_lines, notices = crate_section(crates)
    used = {"MIT", "Apache-2.0", "OFL-1.1"}
    for p in crates:
        used.update(elect(p["license"]))
    texts, sources = license_texts(crates, used)

    out = []
    w = out.append
    w("OPEN-SOURCE LICENSES")
    w("=" * 20)
    w("Commonplace is free software under the Apache License 2.0. It is built from the open-source software")
    w("below. Where a project offers a choice of licenses, the app uses the one named here.")
    w("The full text of each license is at the end. Copyright lines come from each project's license files.")
    w("")
    w("1. COMMONPLACE")
    w("-" * 14)
    w("Commonplace  Apache-2.0  (the LICENSE file of the source repository)")
    w("")
    w("2. NATIVE CODE")
    w("-" * 14)
    w("llama.cpp  MIT")
    w("    Copyright (c) 2023-2026 The ggml authors  (https://github.com/ggml-org/llama.cpp)")
    w("    Includes JSON for Modern C++ by Niels Lohmann (MIT, third_party/llama.cpp/licenses/LICENSE-jsonhpp).")
    w("ONNX Runtime 1.30.0  MIT")
    w("    Copyright (c) Microsoft Corporation  (https://github.com/microsoft/onnxruntime)")
    w("The Rust code links C code from some crates: Zstandard (BSD-3-Clause) and SQLite (public domain).")
    w("LiteRT-LM publishes the notices of its own dependencies in THIRD_PARTY_NOTICE.txt inside its Android library")
    w("(https://github.com/google-ai-edge/LiteRT-LM).")
    w("")
    w("3. MODELS IN THE APP")
    w("-" * 20)
    w("mdbr-leaf-mt  Apache-2.0")
    w("    Query embedder by MongoDB (https://huggingface.co/MongoDB/mdbr-leaf-mt)")
    w("ettin-reranker-17m-v1  Apache-2.0")
    w("    Reranker by the cross-encoder organization, fine-tuned from jhu-clsp/ettin-encoder-17m")
    w("    (https://huggingface.co/cross-encoder/ettin-reranker-17m-v1)")
    w("deberta-v3-xsmall-squad2  Apache-2.0")
    w("    Answer reader by nlpconnect, ONNX export by tomasmcm")
    w("    (https://huggingface.co/nlpconnect/deberta-v3-xsmall-squad2)")
    w("Moonshine Medium Streaming (English)  MIT")
    w("    Voice input model by Moonshine AI. It is not in the app file: you install it as the optional voice-en pack.")
    w("    (https://github.com/moonshine-ai/moonshine)")
    w("")
    w("4. FONT")
    w("-" * 7)
    w("Literata  OFL-1.1")
    w("    Copyright 2017 The Literata Project Authors  (https://github.com/googlefonts/literata)")
    w("")
    w("5. ANDROID LIBRARIES")
    w("-" * 20)
    out += android_section()
    w("")
    w(f"6. RUST CRATES ({len(crates)})")
    w("-" * 18)
    out += crate_lines
    if notices:
        w("")
        w("NOTICE FILES OF RUST CRATES")
        w("-" * 27)
        for name, text in notices:
            w(f"[{name}]")
            w(text)
            w("")
    w("")
    w("LICENSE TEXTS")
    w("=" * 13)
    order = [n for n in PREFERENCE + ["OFL-1.1"] if n in texts]
    for name in order:
        w("")
        w("-" * 72)
        w(name)
        if name in ("MIT", "BSD-3-Clause", "BSD-2-Clause", "ISC", "Zlib"):
            w("The copyright line of each project is listed above. This is the license body they share.")
        w("-" * 72)
        w("")
        w(texts[name])
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print(f"wrote {os.path.relpath(OUT, ROOT)}: {len(crates)} crates, license texts: {', '.join(order)}")
    for n in order:
        print(f"  {n}: {sources[n]}")


if __name__ == "__main__":
    main()
