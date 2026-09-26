"""Design narrator voices with Qwen3-TTS VoiceDesign; the clips become Base-model clone references.

uv run python bench/design_voice.py [--candidates 2]
Writes voices/<name>/cand<k>.wav + ref.txt
"""
import argparse
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hf"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from mlx_audio.tts.utils import load_model  # noqa: E402

from texts import REF_TEXT_EN, REF_TEXT_RU  # noqa: E402

NARRATORS = {
    "ru_male": ("russian", REF_TEXT_RU,
                "A calm, warm, mature male narrator with a low, velvety baritone. Native Russian speaker, "
                "clear standard Moscow pronunciation, measured unhurried pace, professional audiobook reading."),
    "ru_female": ("russian", REF_TEXT_RU,
                  "A warm, gentle adult female narrator with a soft, clear mid-range voice. Native Russian speaker, "
                  "clear standard pronunciation, calm even pace, professional audiobook reading."),
    "en_male": ("english", REF_TEXT_EN,
                "A calm, warm, mature male narrator with a deep, resonant voice. Native British English speaker, "
                "clear diction, measured pace, professional audiobook reading."),
    "en_female": ("english", REF_TEXT_EN,
                  "A warm, gentle adult female narrator with a clear, pleasant mid-range voice. Native American "
                  "English speaker, calm even pace, professional audiobook reading."),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=2)
    ap.add_argument("--model", default="mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-bf16")
    args = ap.parse_args()

    model = load_model(args.model)
    for name, (lang, text, instruct) in NARRATORS.items():
        out = ROOT / "voices" / name
        out.mkdir(parents=True, exist_ok=True)
        (out / "ref.txt").write_text(text + "\n", encoding="utf-8")
        (out / "description.txt").write_text(instruct + "\n", encoding="utf-8")
        for k in range(args.candidates):
            t0 = time.perf_counter()
            res = list(model.generate_voice_design(text=text, instruct=instruct, language=lang))
            audio = np.concatenate([np.array(r.audio) for r in res])
            sr = res[0].sample_rate
            sf.write(out / f"cand{k}.wav", audio, sr)
            dt = time.perf_counter() - t0
            print(f"{name} cand{k}: {len(audio)/sr:.1f}s audio in {dt:.1f}s (RTF {dt/(len(audio)/sr):.2f})")


if __name__ == "__main__":
    main()
