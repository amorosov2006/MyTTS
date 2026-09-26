"""Common paragraph/text cleanup shared by every format parser."""
from __future__ import annotations

import re
import unicodedata

_SOFT_HYPHEN = "­"
_ZERO_WIDTH = ("​", "‌", "‍", "﻿")
_NBSP = " "

_PAGE_NUM_RE = re.compile(r"^[\-–—\s]*(?:page|стр\.?|с\.)?\s*\d{1,4}\s*[\-–—]*\s*$", re.IGNORECASE)
_SCENE_SEP_RE = re.compile(r"^[\*⁂\-–—••·]+$")


def clean_text(text: str) -> str:
    """NFC-normalize and strip invisible junk from a single paragraph's text."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace(_SOFT_HYPHEN, "")
    for z in _ZERO_WIDTH:
        text = text.replace(z, "")
    text = text.replace(_NBSP, " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def is_page_number(text: str) -> bool:
    return bool(_PAGE_NUM_RE.match(text.strip()))


def scene_separator_or_none(text: str) -> str | None:
    """Returns the canonical "* * *" if text is a scene-break marker, else None."""
    stripped = text.strip()
    if not stripped:
        return None
    compact = re.sub(r"\s+", "", stripped)
    if len(compact) >= 2 and _SCENE_SEP_RE.match(compact):
        return "* * *"
    return None


def clean_paragraphs(paragraphs: list[str]) -> list[str]:
    """Clean a chapter's raw paragraph list: normalize, drop empty / page-number
    paragraphs, canonicalize scene separators."""
    out: list[str] = []
    for raw in paragraphs:
        text = clean_text(raw)
        if not text:
            continue
        sep = scene_separator_or_none(text)
        if sep is not None:
            out.append(sep)
            continue
        if is_page_number(text):
            continue
        out.append(text)
    return out
