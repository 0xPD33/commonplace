#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["sherpa-onnx==2.0.1", "moonshine-voice==0.1.5", "numpy", "soundfile"]
# ///
"""Desktop end-to-end check of the read-aloud voice (Pocket TTS int8 through sherpa-onnx, the tts-en pack).

Synthesizes a sample answer with the app's settings (2 threads, 5 steps, temperature 0.3, the pack's voice.wav) and the streaming
callback the app plays from. Checks the real-time factor, the time to the first sentence and memory, then transcribes the
audio with Moonshine (the voice-input model) and checks the word error rate against the text.
Writes artifacts/tts/<stamp>/{answer.wav,report.json,transcript.txt}.

Usage: scripts/fetch-models.sh tts stt && scripts/e2e_tts.py   (uses data/library/packs/tts-en when it is built)
"""

from __future__ import annotations

import json
import resource
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import sherpa_onnx as so
import soundfile as sf
from moonshine_voice import ModelArch
from moonshine_voice.transcriber import Transcriber, TranscriptEventListener

ROOT = Path(__file__).resolve().parent.parent
VOICE = next((d for d in (ROOT / "data/library/packs/tts-en", ROOT / "data/models/tts/pocket-tts-int8") if (d / "lm_main.int8.onnx").is_file()), None)
STT = ROOT / "data/models/stt/moonshine-medium-streaming-en"
TEXT = (
    "The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris. "
    "Gustave Eiffel's company designed and built it for the 1889 World's Fair. "
    "It is 330 metres tall, and it was the tallest man-made structure in the world for 41 years, "
    "until the Chrysler Building in New York was finished in 1930. About 6 million people visit it each year."
)

# Pass thresholds. Speed is loose: the desktop shares its CPU with other jobs, and a phone core is slower.
# The voice samples its output, so the WER of the round trip varies from run to run (about 8-15%).
MAX_RTF = 0.5
MAX_FIRST_AUDIO_S = 2.0
MAX_WER = 0.20
MAX_RSS_MB = 900

sys.path.insert(0, str(ROOT / "scripts"))
from e2e_stt import wer, words  # noqa: E402


class Recorder(TranscriptEventListener):
    def __init__(self) -> None:
        self.finals: list[str] = []

    def on_line_completed(self, e) -> None:
        self.finals.append(e.line.text)


def transcribe(audio: np.ndarray, sr: int) -> str:
    n = int(len(audio) * 16000 / sr)
    a16 = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
    t = Transcriber(model_path=str(STT), model_arch=ModelArch.MEDIUM_STREAMING)
    rec = Recorder()
    t.add_listener(rec)
    t.start()
    for i in range(0, len(a16), 1600):
        t.add_audio(a16[i : i + 1600].tolist(), 16000)
    t.stop()
    return " ".join(rec.finals)


def main() -> int:
    if VOICE is None:
        sys.exit("voice missing: run scripts/fetch-models.sh tts")
    if not STT.is_dir():
        sys.exit(f"Moonshine missing: run scripts/fetch-models.sh stt ({STT})")
    d = VOICE
    model = so.OfflineTtsModelConfig(num_threads=2, provider="cpu")
    model.pocket = so.OfflineTtsPocketModelConfig(
        f"{d}/lm_flow.int8.onnx", f"{d}/lm_main.int8.onnx", f"{d}/encoder.onnx", f"{d}/decoder.int8.onnx",
        f"{d}/text_conditioner.onnx", f"{d}/vocab.json", f"{d}/token_scores.json",
    )
    t0 = time.perf_counter()
    tts = so.OfflineTts(so.OfflineTtsConfig(model=model))
    load_s = time.perf_counter() - t0
    voice, voice_sr = sf.read(d / "voice.wav", dtype="float32")
    gen = so.GenerationConfig()
    gen.num_steps, gen.reference_audio, gen.reference_sample_rate = 5, voice.tolist(), voice_sr
    gen.extra = {"temperature": "0.3"}
    blocks: list[tuple[float, int]] = []
    t0 = time.perf_counter()

    def on_samples(samples, progress=None) -> int:
        blocks.append((time.perf_counter() - t0, len(samples)))
        return 1

    audio = tts.generate(TEXT, gen, callback=on_samples)
    synth_s = time.perf_counter() - t0
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    samples = np.asarray(audio.samples, dtype=np.float32)
    audio_s = len(samples) / audio.sample_rate
    heard = transcribe(samples, audio.sample_rate)
    speech_wer = wer(words(TEXT), words(heard))

    r = {
        "voice": str(d.relative_to(ROOT)),
        "sherpa_onnx": "2.0.1",
        "threads": 2,
        "sample_rate": audio.sample_rate,
        "load_s": round(load_s, 2),
        "synth_s": round(synth_s, 2),
        "audio_s": round(audio_s, 2),
        "rtf": round(synth_s / audio_s, 3),
        "callbacks": len(blocks),
        "first_audio_s": round(blocks[0][0], 2) if blocks else None,
        "rss_mb_peak_before_asr": round(peak_mb),
        "wer": round(speech_wer, 4),
        "heard": heard,
    }
    checks = {
        f"RTF <= {MAX_RTF}": r["rtf"] <= MAX_RTF,
        "audio arrives in pieces": len(blocks) > 1,
        f"first audio in <= {MAX_FIRST_AUDIO_S}s": bool(blocks) and blocks[0][0] <= MAX_FIRST_AUDIO_S,
        f"WER <= {MAX_WER:.0%}": speech_wer <= MAX_WER,
        f"peak RSS <= {MAX_RSS_MB} MB": peak_mb <= MAX_RSS_MB,
    }
    r["checks"] = checks

    out = ROOT / "artifacts/tts" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    sf.write(out / "answer.wav", samples, samplerate=audio.sample_rate, subtype="PCM_16")
    (out / "report.json").write_text(json.dumps(r, indent=2) + "\n")
    (out / "transcript.txt").write_text(f"text:\n{TEXT}\n\nheard (Moonshine):\n{heard}\n")
    print(json.dumps(r, indent=2))
    print(f"artifact: {out}")
    failed = [k for k, ok in checks.items() if not ok]
    print("FAIL: " + "; ".join(failed) if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
