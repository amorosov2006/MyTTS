"""Character/word error rate scoring for ASR-based QA.

Levenshtein + normalization logic ported from bench/asr_check.py (proven on Phase 0 data).
`char_error_rate`/`word_error_rate` score two ALREADY-normalized strings -- callers pass the
output of `mytts.text.compare_form` (numbers spelled out, ё restored, etc.) when available.
`normalize`/`cer`/`wer` provide the same convenience standalone scoring bench/asr_check.py used,
for callers with no access to compare_form.
"""
from __future__ import annotations

import re

_YO_MAP = str.maketrans({"ё": "е", "Ё": "Е"})
_DASH_RE = re.compile(r"[‐-―−]")  # hyphen/dash variants -> "-"
_QUOTE_RE = re.compile(r"[«»“”„‘’]")  # quotes -> ""
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fallback canonical form: lowercase, ё->е, dash/quote unification, punctuation stripped,
    whitespace collapsed. Does NOT spell out digits -- prefer mytts.text.compare_form for that."""
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


def char_error_rate(ref_form: str, hyp_form: str) -> float:
    """CER between two already-normalized strings."""
    ref_chars = list(ref_form.replace(" ", ""))
    hyp_chars = list(hyp_form.replace(" ", ""))
    if not ref_chars:
        return 0.0 if not hyp_chars else 1.0
    return _levenshtein(ref_chars, hyp_chars) / len(ref_chars)


def word_error_rate(ref_form: str, hyp_form: str) -> float:
    """WER between two already-normalized strings."""
    ref_words, hyp_words = ref_form.split(), hyp_form.split()
    if not ref_words:
        return 0.0 if not hyp_words else 1.0
    return _levenshtein(ref_words, hyp_words) / len(ref_words)


def cer(ref: str, hyp: str) -> float:
    """Normalize both sides with the fallback `normalize()`, then score."""
    return char_error_rate(normalize(ref), normalize(hyp))


def wer(ref: str, hyp: str) -> float:
    return word_error_rate(normalize(ref), normalize(hyp))
