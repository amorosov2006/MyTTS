"""Offline ASR verification: transcribe a rendered wav with Whisper (MLX) and
score it against the expected text with CER/WER.

Usage:
    uv run python bench/asr_check.py <dir>

<dir> must contain pairs `name.wav` + `name.txt` (the .txt holds the source
text that was sent to the TTS engine). Writes <dir>/asr_report.json and
prints a table, including ASR time per second of audio (QA overhead).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path(__file__).resolve().parent.parent / "models" / "hf"))

import numpy as np
import soundfile as sf

MODEL = "mlx-community/whisper-large-v3-turbo"

_model_cache: dict[str, object] = {}


# ---------------------------------------------------------------- normalize

_YO_MAP = str.maketrans({"ё": "е", "Ё": "Е"})
_DASH_RE = re.compile(r"[‐-―−]")  # hyphen/dash variants -> "-"
_QUOTE_RE = re.compile(r"[«»“”„‘’]")  # quotes -> ""
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = text.lower().translate(_YO_MAP)
    text = _DASH_RE.sub("-", text)
    text = _QUOTE_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _levenshtein(a: list, b: list) -> int:
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    ref_n, hyp_n = normalize(ref), normalize(hyp)
    ref_chars = list(ref_n.replace(" ", ""))
    hyp_chars = list(hyp_n.replace(" ", ""))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _levenshtein(ref_chars, hyp_chars) / len(ref_chars)


def wer(ref: str, hyp: str) -> float:
    ref_words = normalize(ref).split()
    hyp_words = normalize(hyp).split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _levenshtein(ref_words, hyp_words) / len(ref_words)


# ---------------------------------------------------------------- whisper

def _load_model():
    # mlx_whisper caches models internally by path_or_hf_repo; nothing to
    # eagerly construct here, but keep a marker so we only warm up once.
    if MODEL not in _model_cache:
        import mlx_whisper  # noqa: F401  (import triggers weight fetch on first use)

        _model_cache[MODEL] = True
    return _model_cache[MODEL]


def transcribe(wav_path: str, lang: str) -> str:
    import mlx_whisper

    _load_model()
    result = mlx_whisper.transcribe(
        wav_path, path_or_hf_repo=MODEL, language=lang, verbose=False
    )
    return result["text"].strip()


def check(wav_path: str, expected_text: str, lang: str) -> dict:
    t0 = time.time()
    transcript = transcribe(wav_path, lang)
    asr_time = time.time() - t0

    info = sf.info(wav_path)
    audio_sec = info.frames / info.samplerate

    return {
        "wav": wav_path,
        "transcript": transcript,
        "cer": round(cer(expected_text, transcript), 4),
        "wer": round(wer(expected_text, transcript), 4),
        "asr_time_sec": round(asr_time, 3),
        "audio_sec": round(audio_sec, 3),
        "asr_rtf": round(asr_time / audio_sec, 4) if audio_sec > 0 else None,
    }


def _guess_lang(text: str) -> str:
    return "ru" if re.search(r"[Ѐ-ӿ]", text) else "en"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <dir>", file=sys.stderr)
        return 1

    d = Path(argv[1])
    wavs = sorted(d.glob("*.wav"))
    if not wavs:
        print(f"no .wav files found in {d}", file=sys.stderr)
        return 1

    results = []
    for wav in wavs:
        txt = wav.with_suffix(".txt")
        if not txt.exists():
            print(f"skip {wav.name}: no matching .txt")
            continue
        expected = txt.read_text(encoding="utf-8").strip()
        lang = _guess_lang(expected)
        r = check(str(wav), expected, lang)
        r["name"] = wav.stem
        r["lang"] = lang
        results.append(r)
        print(
            f"{wav.stem:30s} lang={lang} CER={r['cer']:.3f} WER={r['wer']:.3f} "
            f"asr={r['asr_time_sec']:.2f}s/{r['audio_sec']:.2f}s (rtf={r['asr_rtf']})"
        )

    if results:
        avg_cer = sum(r["cer"] for r in results) / len(results)
        avg_wer = sum(r["wer"] for r in results) / len(results)
        avg_rtf = sum(r["asr_rtf"] for r in results if r["asr_rtf"] is not None) / len(results)
        print(f"\naverage CER={avg_cer:.3f} WER={avg_wer:.3f} asr_rtf={avg_rtf:.3f}")

    out = d / "asr_report.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
