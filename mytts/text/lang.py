"""Language detection: Cyrillic vs Latin letter ratio."""
from __future__ import annotations

import re

from mytts.contracts import Lang

_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def detect_lang(text: str) -> Lang:
    """Count Cyrillic vs Latin letters and pick the majority script.

    Ties and letter-free text (numbers, punctuation only, empty string) default
    to ru: this is a Russian-first project (default voice/book language is ru),
    so an undecidable snippet is treated as ru rather than en.
    """
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    return Lang.en if lat > cyr else Lang.ru
