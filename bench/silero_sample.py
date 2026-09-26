"""Render the Russian listening-test sample with Silero TTS (v5_5_ru) so it
can be compared against Qwen3-TTS.

Renders PROSE_RU[0:4] and all of TRAPS_RU with two voices (aidar = male,
xenia = female) at 48 kHz, using Silero's built-in stress/homograph/yo
restoration (on by default in v5_5_ru's apply_tts). Writes wav+txt pairs to
bench/out/silero/<voice>/<name>.{wav,txt} and timing to
bench/out/silero/timing.json.

Requires the `silero` dependency group: `uv run --group silero python bench/silero_sample.py`
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import soundfile as sf
import torch

from texts import PROSE_RU, TRAPS_RU

OUT_DIR = Path(__file__).parent / "out" / "silero"
SAMPLE_RATE = 48000
VOICES = [("aidar", "male"), ("xenia", "female")]
MODEL_VERSION = "v5_5_ru"


def load_model():
    model, _ = torch.hub.load(
        repo_or_dir="snakers4/silero-models",
        model="silero_tts",
        language="ru",
        speaker=MODEL_VERSION,
        trust_repo=True,
        source="github",
    )
    return model


def check_mps(sample_text: str) -> dict:
    """Try MPS on a throwaway model instance (moving the shared jit model to
    'mps' can leave it in a half-moved, broken state on failure, so this
    never touches the model used for the real CPU renders)."""
    info = {"mps_available_in_torch": torch.backends.mps.is_available()}
    try:
        probe = load_model()
        probe.to("mps")
        t0 = time.time()
        audio = probe.apply_tts(text=sample_text, speaker="aidar", sample_rate=SAMPLE_RATE)
        dt = time.time() - t0
        info["mps_works"] = True
        info["mps_rtf"] = round(dt / (len(audio) / SAMPLE_RATE), 4)
    except Exception as e:
        info["mps_works"] = False
        info["mps_error"] = repr(e)
    return info


def main() -> None:
    segments = [(f"prose_{i}", t) for i, t in enumerate(PROSE_RU[:4])]
    segments += [(f"trap_{i}", t) for i, t in enumerate(TRAPS_RU)]

    timing = {
        "model": MODEL_VERSION,
        "sample_rate": SAMPLE_RATE,
        "device_check": check_mps(segments[0][1]),
        "cpu": [],
    }

    model = load_model()

    for voice, gender in VOICES:
        voice_dir = OUT_DIR / voice
        voice_dir.mkdir(parents=True, exist_ok=True)
        for name, text in segments:
            t0 = time.time()
            audio = model.apply_tts(text=text, speaker=voice, sample_rate=SAMPLE_RATE)
            gen_time = time.time() - t0
            audio_sec = len(audio) / SAMPLE_RATE

            wav_path = voice_dir / f"{name}.wav"
            sf.write(str(wav_path), audio.numpy(), SAMPLE_RATE)
            (voice_dir / f"{name}.txt").write_text(text, encoding="utf-8")

            rec = {
                "voice": voice,
                "gender": gender,
                "name": name,
                "gen_time_sec": round(gen_time, 4),
                "audio_sec": round(audio_sec, 4),
                "rtf": round(gen_time / audio_sec, 4),
            }
            timing["cpu"].append(rec)
            print(f"{voice:6s} {name:10s} rtf={rec['rtf']:.4f} ({gen_time:.2f}s / {audio_sec:.2f}s)")

    rtfs = [r["rtf"] for r in timing["cpu"]]
    timing["avg_rtf_cpu"] = round(sum(rtfs) / len(rtfs), 4)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "timing.json").write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\navg CPU rtf={timing['avg_rtf_cpu']}, device_check={timing['device_check']}")
    print(f"wrote {OUT_DIR / 'timing.json'}")


if __name__ == "__main__":
    main()
