"""Shared chapter-drafting helpers used by every format parser.

Each format parser builds a list of `ChapterDraft` (title / raw paragraphs / kind /
include) plus book-level metadata, then hands it to `finalize_book`, which does the
common cleanup: paragraph cleaning, dropping empties, oversized-chapter splitting,
sequential indexing and language detection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from mytts.contracts import Book, Chapter, IngestError, Lang

from ._clean import clean_paragraphs
from ._lang import detect_lang

Kind = Literal["body", "front", "back", "notes", "toc"]

MAX_CHAPTER_CHARS = 60_000
SPLIT_PART_CHARS = 20_000

_TOC_KEYWORDS = re.compile(r"содержани|оглавлени|table of contents|\bcontents\b", re.IGNORECASE)
_NOTES_KEYWORDS = re.compile(r"примечани|сноск|footnotes?|endnotes?|\bnotes\b", re.IGNORECASE)
_BACK_KEYWORDS = re.compile(
    r"об авторе|about the author|благодарност|acknowledg|глоссари|glossary", re.IGNORECASE
)
_FRONT_KEYWORDS = re.compile(
    r"обложка|\bcover\b|титульный лист|title page|копирайт|copyright|\bisbn\b|"
    r"аннотаци|annotation|посвящ|dedication|©",
    re.IGNORECASE,
)

CHAPTER_HEADING_RE = re.compile(
    r"^(глава|часть|пролог|эпилог|книга|chapter|part|prologue|epilogue|book)\b"
    r"[\s.:,—–-]*([ivxlcdm]+|\d+)?[\s.:,—–-]*(.*)$",
    re.IGNORECASE,
)
_ROMAN_ONLY_RE = re.compile(r"^[ivxlcdm]{1,7}\.?$", re.IGNORECASE)
_NUMBER_ONLY_RE = re.compile(r"^\d{1,3}\.?$")


def classify_kind(title: str) -> Kind:
    t = title.strip()
    if not t:
        return "body"
    if _TOC_KEYWORDS.search(t):
        return "toc"
    if _NOTES_KEYWORDS.search(t):
        return "notes"
    if _BACK_KEYWORDS.search(t):
        return "back"
    if _FRONT_KEYWORDS.search(t):
        return "front"
    return "body"


def looks_like_heading(line: str) -> bool:
    s = line.strip()
    if not s or len(s) > 80:
        return False
    if CHAPTER_HEADING_RE.match(s):
        return True
    if _ROMAN_ONLY_RE.match(s):
        return True
    if _NUMBER_ONLY_RE.match(s):
        return True
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 3 and all(c.isupper() for c in letters):
        return True
    return False


@dataclass
class ChapterDraft:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    kind: Kind = "body"
    include: bool | None = None  # None => derive from kind (body => True)


def _part_title(n: int, lang: Lang) -> str:
    return f"Часть {n}" if lang == Lang.ru else f"Part {n}"


def _split_oversized(draft: ChapterDraft, lang: Lang) -> list[ChapterDraft]:
    total = sum(len(p) for p in draft.paragraphs)
    if total <= MAX_CHAPTER_CHARS:
        return [draft]
    parts: list[ChapterDraft] = []
    cur: list[str] = []
    cur_chars = 0
    part_no = 1
    for para in draft.paragraphs:
        if cur and cur_chars + len(para) > SPLIT_PART_CHARS:
            parts.append(ChapterDraft(_part_title(part_no, lang), cur, draft.kind, draft.include))
            part_no += 1
            cur, cur_chars = [], 0
        cur.append(para)
        cur_chars += len(para)
    if cur:
        parts.append(ChapterDraft(_part_title(part_no, lang), cur, draft.kind, draft.include))
    return parts


def finalize_book(
    *,
    title: str,
    author: str | None,
    source_path: str,
    source_format: str,
    cover: bytes | None,
    cover_mime: str | None,
    drafts: list[ChapterDraft],
    warnings: list[str] | None = None,
) -> Book:
    """Common post-processing: clean paragraphs, drop empty chapters, split an
    oversized lone chapter, index sequentially, detect language."""
    warnings = list(warnings or [])

    cleaned: list[ChapterDraft] = []
    for d in drafts:
        paras = clean_paragraphs(d.paragraphs)
        if not paras:
            continue
        cleaned.append(ChapterDraft(d.title.strip(), paras, d.kind, d.include))

    if not cleaned:
        raise IngestError("The book appears to be empty: no readable text was found.")

    # detect language up front so a lone oversized chapter gets the right part title
    all_text = " ".join(p for d in cleaned for p in d.paragraphs)
    lang = detect_lang(all_text)

    if len(cleaned) == 1 and cleaned[0].kind == "body":
        cleaned = _split_oversized(cleaned[0], lang)

    chapters: list[Chapter] = []
    for i, d in enumerate(cleaned):
        include = d.include if d.include is not None else (d.kind == "body")
        chapters.append(
            Chapter(index=i, title=d.title, paragraphs=d.paragraphs, include=include, kind=d.kind)
        )

    return Book(
        title=title.strip() or _fallback_title(source_path),
        author=author.strip() if author else None,
        lang=lang,
        source_path=source_path,
        source_format=source_format,
        cover=cover,
        cover_mime=cover_mime,
        chapters=chapters,
        warnings=warnings,
    )


def _fallback_title(source_path: str) -> str:
    from pathlib import Path

    return Path(source_path).stem
