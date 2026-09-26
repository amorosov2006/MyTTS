"""Text pipeline: clean chapter paragraphs -> normalized TTS segments.

Public API (see mytts/contracts.py for the full contract):
  detect_lang(text) -> Lang
  normalize(text, lang) -> str
  prepare_chapter(chapter, lang, ...) -> list[Segment]
  compare_form(text, lang) -> str
"""
from mytts.text.compare import compare_form
from mytts.text.lang import detect_lang
from mytts.text.segment import normalize, prepare_chapter

__all__ = ["detect_lang", "normalize", "prepare_chapter", "compare_form"]
