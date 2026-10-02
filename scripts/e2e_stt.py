#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["moonshine-voice==0.1.5", "numpy"]
# ///
"""Desktop end-to-end check of the voice-input model (Moonshine Medium Streaming EN).

Streams eval/stt/two_cities_16k.wav in 100 ms chunks at real time, like a microphone.
Checks WER and partials on the whole clip, then taps stop in the middle of a sentence
and times the flush to final text. Also checks memory and silence.
Writes artifacts/stt/<stamp>/{report.json,transcript.txt}.

Usage: scripts/fetch-models.sh stt && scripts/e2e_stt.py [--fast]   (--fast: no real-time pacing)
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from moonshine_voice import ModelArch, load_wav_file
from moonshine_voice.transcriber import Transcriber, TranscriptEventListener

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "data/models/stt/moonshine-medium-streaming-en"
WAV = ROOT / "eval/stt/two_cities_16k.wav"
REF = ROOT / "eval/stt/two_cities.txt"
CHUNK_S = 0.1

# Pass thresholds. Latency is loose: the desktop shares its CPU with other jobs.
MAX_WER = 0.10
MAX_STOP_TO_FINAL_S = 1.5
MAX_RSS_MB = 1024


def words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]", " ", text.lower().replace("-", " ")).split()


def wer(ref: list[str], hyp: list[str]) -> float:
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[-1] / len(ref)


class Recorder(TranscriptEventListener):
    def __init__(self) -> None:
        self.t0 = 0.0
        self.partials: list[tuple[float, str]] = []
        self.finals: list[tuple[float, str]] = []
        self.spans: list[tuple[float, float]] = []  # (start, duration) in audio seconds

    def on_line_text_changed(self, e) -> None:
        self.partials.append((time.perf_counter() - self.t0, e.line.text))

    def on_line_completed(self, e) -> None:
        self.finals.append((time.perf_counter() - self.t0, e.line.text))
        self.spans.append((e.line.start_time, e.line.duration))


def rss_mb() -> float:
    return int(Path("/proc/self/statm").read_text().split()[1]) * 4096 / 1e6


def stream(t: Transcriber, rec: Recorder, audio, sr: int, pace: bool) -> dict:
    """Feed audio like a mic, tap stop after the last chunk. Returns timing facts."""
    step = int(CHUNK_S * sr)
    add_ms: list[float] = []
    t.start()
    rec.t0 = start = time.perf_counter()
    for n, i in enumerate(range(0, len(audio), step)):
        if pace:  # wait until this chunk would have been captured
            time.sleep(max(0.0, start + (n + 1) * CHUNK_S - time.perf_counter()))
        c0 = time.perf_counter()
        t.add_audio(audio[i : i + step], sr)
        add_ms.append((time.perf_counter() - c0) * 1000)
    tap = time.perf_counter()
    t.stop()
    done = time.perf_counter()
    last_final = rec.finals[-1][0] + rec.t0 if rec.finals else done
    return {
        "stop_call_s": done - tap,
        "stop_to_last_final_s": max(0.0, last_final - tap),
        "add_audio_ms_mean": float(np.mean(add_ms)),
        "add_audio_ms_max": float(np.max(add_ms)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="feed audio without real-time pacing")
    args = ap.parse_args()
    if not MODEL.is_dir():
        sys.exit(f"model missing: run scripts/fetch-models.sh stt ({MODEL})")

    t0 = time.perf_counter()
    rss_before = rss_mb()
    t = Transcriber(model_path=str(MODEL), model_arch=ModelArch.MEDIUM_STREAMING)
    load_s = time.perf_counter() - t0
    rss_loaded = rss_mb()
    rec = Recorder()
    t.add_listener(rec)

    audio, sr = load_wav_file(WAV)
    speech = stream(t, rec, audio, sr, pace=not args.fast)
    hyp = " ".join(text for _, text in rec.finals)
    first_partial = rec.partials[0][0] if rec.partials else None
    speech_wer = wer(words(REF.read_text()), words(hyp))

    # Tap stop in the middle of a sentence: the flush must still give final text quickly.
    tap_rec = Recorder()
    t.remove_all_listeners()
    t.add_listener(tap_rec)
    (s0, _), (s1, d1) = rec.spans[-5], rec.spans[-4]
    lo, hi = int((s0 - 0.4) * sr), int((s1 + 0.7 * d1) * sr)
    tap = stream(t, tap_rec, audio[lo:hi], sr, pace=not args.fast)
    tap_text = " ".join(text for _, text in tap_rec.finals).strip()

    # Silence plus faint noise must not produce words (Whisper-style hallucination check).
    rec_quiet = Recorder()
    t.remove_all_listeners()
    t.add_listener(rec_quiet)
    noise = (np.random.default_rng(0).standard_normal(8 * sr) * 0.002).astype(np.float32).tolist()
    stream(t, rec_quiet, noise, sr, pace=False)
    quiet_text = " ".join(text for _, text in rec_quiet.finals).strip()

    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    r = {
        "model": MODEL.name,
        "audio_s": len(audio) / sr,
        "paced_real_time": not args.fast,
        "load_s": round(load_s, 2),
        "wer": round(speech_wer, 4),
        "lines": len(rec.finals),
        "partial_updates": len(rec.partials),
        "first_partial_s": None if first_partial is None else round(first_partial, 2),
        "tap_mid_sentence_text": tap_text,
        "tap_stop_call_s": round(tap["stop_call_s"], 3),
        "tap_partial_updates": len(tap_rec.partials),
        "add_audio_ms_mean": round(speech["add_audio_ms_mean"], 1),
        "add_audio_ms_max": round(speech["add_audio_ms_max"], 1),
        "rss_mb_before_load": round(rss_before),
        "rss_mb_after_load": round(rss_loaded),
        "rss_mb_peak": round(peak_mb),
        "silence_text": quiet_text,
    }
    checks = {
        f"WER <= {MAX_WER:.0%}": speech_wer <= MAX_WER,
        "partials arrive before the final": first_partial is not None and first_partial < rec.finals[0][0],
        "mid-sentence tap gives final text": len(words(tap_text)) >= 5,
        f"mid-sentence tap flushes in <= {MAX_STOP_TO_FINAL_S}s": tap["stop_call_s"] <= MAX_STOP_TO_FINAL_S,
        "add_audio keeps up with the mic": speech["add_audio_ms_mean"] < CHUNK_S * 1000,
        f"peak RSS <= {MAX_RSS_MB} MB": peak_mb <= MAX_RSS_MB,
        "silence gives no text": quiet_text == "",
    }
    r["checks"] = checks

    out = ROOT / "artifacts/stt" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(r, indent=2) + "\n")
    (out / "transcript.txt").write_text(
        "\n".join(f"[{ts:6.2f}s] {text}" for ts, text in rec.finals) + f"\n\nmid-sentence tap: {tap_text}\n\nreference:\n{REF.read_text()}"
    )
    print(json.dumps(r, indent=2))
    print(f"artifact: {out}")
    failed = [k for k, ok in checks.items() if not ok]
    print("FAIL: " + "; ".join(failed) if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
