"""Offline ASR verification (mlx_whisper) implementing contracts.Verifier.

Real-model code: only run under bench/memguard.py, one process at a time (CLAUDE.md).
"""
from __future__ import annotations

import numpy as np
from scipy.signal import resample_poly

from mytts import config
from mytts.contracts import Lang
from mytts.tts.cer import char_error_rate, normalize

_WHISPER_LANG = {Lang.ru: "ru", Lang.en: "en"}
_WHISPER_SR = 16000


def _compare_form(text: str, lang: Lang) -> str:
    """mytts.text (written in parallel, not present in every worktree) owns the canonical QA
    form: lowercase, ё->е, digits->words, punctuation stripped. Fall back to the punctuation/case
    normalizer if it isn't importable yet."""
    try:
        from mytts.text import compare_form
        return compare_form(text, lang)
    except ImportError:
        return normalize(text)


class WhisperVerifier:
    """mlx-whisper large-v3-turbo. Loaded lazily on first `check()`."""

    def __init__(self, model_id: str = config.ASR_MODEL):
        self.model_id = model_id

    def check(self, audio: np.ndarray, sample_rate: int, text: str, lang: Lang) -> tuple[float, str]:
        import mlx_whisper

        audio = np.asarray(audio, dtype=np.float32)
        if sample_rate != _WHISPER_SR:
            audio = resample_poly(audio, _WHISPER_SR, sample_rate).astype(np.float32)

        result = mlx_whisper.transcribe(
            audio, path_or_hf_repo=self.model_id, language=_WHISPER_LANG[lang], verbose=False
        )
        transcript = result["text"].strip()
        score = char_error_rate(_compare_form(text, lang), _compare_form(transcript, lang))
        return score, transcript
