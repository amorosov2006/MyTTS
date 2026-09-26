"""Canonical form used to score ASR QA transcripts against the spoken text."""
from __future__ import annotations

import re

from mytts.contracts import Lang
from mytts.text.normalize_en import normalize as _normalize_en
from mytts.text.normalize_ru import normalize as _normalize_ru
from mytts.text.segment import expand_ru_arabic_chapter_label

_NON_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def compare_form(text: str, lang: Lang) -> str:
    """lowercase, ё -> е, digits -> words, punctuation stripped, whitespace
    collapsed. Applied to BOTH the spoken text and the ASR transcript, so a
    transcript that writes a number as digits ("1891") lines up with text
    that spelled it out ("тысяча восемьсот девяносто первом") -- see the
    Phase 0 finding in PLAN.md.

    Digits are converted by running the SAME normalize_ru/normalize_en
    pipeline used for TTS input, not a separate plain-cardinal reading: a
    bare "1891" next to "году" in a transcript needs the year/ordinal
    reading, not just any digit-to-word mapping, to land close to what the
    source actually says (case endings may still differ -- that's fine, CER
    tolerates it). normalize() is idempotent on already-normalized text (no
    digits/abbreviations left to touch), so calling it on the source side is
    a safe no-op.

    A chapter/part title needs one extra step on the RU side first: "Глава 2"
    must read as the ordinal "Глава вторая" (matching normalize_title), not
    the plain cardinal "Глава два" normalize_ru's generic digit pass would
    otherwise produce -- otherwise a QA transcript that writes the title with
    a digit ("Глава 2") scores a spuriously high CER against the spoken
    "Глава вторая". Roman numerals ("Глава II") and EN ("Chapter 4"/
    "Chapter IV" -> "Chapter four" either way) already come out consistent
    through the ordinary pipeline, so they need no extra step here.
    """
    if lang == Lang.ru:
        text = expand_ru_arabic_chapter_label(text)
    text = _normalize_ru(text) if lang == Lang.ru else _normalize_en(text)
    text = text.lower()
    if lang == Lang.ru:
        text = text.replace("ё", "е")
    text = _NON_WORD_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text
