"""Does Qwen3-TTS pause inside Russian hyphenated words (что-то, кое-кто)? Compare spellings.

uv run python bench/memguard.py --limit-gb 12 -- uv run python bench/hyphen_test.py
Metric: silent gaps >= 120 ms inside the utterance (commas give a constant baseline), plus
Whisper transcript (does the joined spelling still sound like "что-то"?).
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("HF_HOME", str(ROOT / "models" / "hf"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from mytts.contracts import Lang, SynthesisParams, Voice  # noqa: E402
from mytts.tts.qwen_mlx import QwenEngine  # noqa: E402
from mytts.tts.whisper_qa import WhisperVerifier  # noqa: E402

SENTENCES = [
    "Надо было что-то делать, но никто не знал, что именно.",
    "Кое-кто уже спал, а как-нибудь потом всё решится само.",
    "Он всё-таки пришёл, и где-то вдалеке кто-то засмеялся.",
]
VARIANTS = {
    "A hyphen-minus": lambda s: s,
    "B non-breaking hyphen U+2011": lambda s: s.replace("-", "‑"),
    "C joined": lambda s: s.replace("-", ""),
    "D zero-width joiner": lambda s: s.replace("-", "‍"),
}
REPEATS = 3
SR = 24000


def gaps(audio: np.ndarray, min_ms=120, thresh_db=-40.0) -> list[int]:
    hop = SR // 100  # 10 ms frames
    frames = audio[: len(audio) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)
    voiced = np.where(db > thresh_db)[0]
    if len(voiced) < 2:
        return []
    inside = db[voiced[0]: voiced[-1] + 1] <= thresh_db
    runs, n = [], 0
    for silent in inside:
        if silent:
            n += 1
        else:
            if n * 10 >= min_ms:
                runs.append(n * 10)
            n = 0
    return runs


def main():
    out = ROOT / "bench" / "out" / "hyphen"
    out.mkdir(parents=True, exist_ok=True)
    engine = QwenEngine()
    engine.load()
    verifier = WhisperVerifier()
    for vid in ("ru_male", "ru_female"):
        meta = __import__("json").loads((ROOT / "voices" / vid / "voice.json").read_text())
        voice = Voice(**{**meta, "ref_audio": str(ROOT / "voices" / vid / "ref.wav")})
        print(f"\n=== voice {vid}")
        for name, fn in VARIANTS.items():
            texts = [fn(s) for s in SENTENCES for _ in range(REPEATS)]
            outs = engine.synthesize(texts, Lang.ru, voice, SynthesisParams())
            g = [gaps(o.audio) for o in outs]
            n_gaps = np.mean([len(x) for x in g])
            words = []
            for i, (t, o) in enumerate(zip(texts, outs)):
                sf.write(out / f"{vid}_{name[0]}_{i}.wav", o.audio, SR)
                if i % REPEATS == 0:
                    _, tr = verifier.check(o.audio, SR, SENTENCES[i // REPEATS], Lang.ru)
                    words.append(tr)
            print(f"{name:32s} gaps/sentence={n_gaps:.2f}  gap ms={sorted(sum(g, []))[-5:]}")
            for w in words:
                print(f"    ASR: {w}")


if __name__ == "__main__":
    main()
