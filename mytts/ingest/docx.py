"""DOCX parser: python-docx, Heading styles split chapters."""
from __future__ import annotations

from pathlib import Path

import docx as _pydocx

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, classify_kind, finalize_book


def _heading_level(style_name: str) -> int | None:
    name = (style_name or "").lower()
    if name.startswith("title"):
        return 0
    if name.startswith("heading 1"):
        return 1
    if name.startswith("heading 2"):
        return 2
    return None


def parse_book(path: Path) -> Book:
    try:
        doc = _pydocx.Document(str(path))
    except Exception as e:
        raise IngestError(f"This DOCX file could not be read (corrupt file): {e}") from e

    core = doc.core_properties
    title = (core.title or "").strip()
    author = (core.author or "").strip() or None

    paragraphs = [(p.text, _heading_level(p.style.name if p.style else "")) for p in doc.paragraphs]

    # choose the split level: prefer Heading 1; use Heading 2 only if there's no Heading 1
    h1_count = sum(1 for _, lvl in paragraphs if lvl == 1)
    split_level = 1 if h1_count >= 1 else 2

    drafts: list[ChapterDraft] = []
    current_title = ""
    current_paras: list[str] = []
    started = False

    def flush():
        if started:
            drafts.append(ChapterDraft(current_title, list(current_paras), "body", None))

    for text, level in paragraphs:
        if level is not None and (level == 0 or level == split_level):
            flush()
            current_title = text.strip()
            current_paras = []
            started = True
        elif started and text.strip():
            current_paras.append(text)
    flush()

    # anything before the very first heading becomes an explicit front-matter draft
    lead = []
    for text, level in paragraphs:
        if level is not None:
            break
        if text.strip():
            lead.append(text)
    if lead:
        drafts.insert(0, ChapterDraft(title or "Front matter", lead, "front", False))

    # the book-title paragraph (styled like a chapter heading) is front matter, not a chapter
    if drafts and drafts[0].kind == "body" and title and drafts[0].title.strip().lower() == title.strip().lower():
        drafts[0].kind = "front"
        drafts[0].include = False

    for d in drafts:
        if d.kind == "body":
            k = classify_kind(d.title)
            if k != "body":
                d.kind = k
                d.include = False

    if not drafts:
        raise IngestError("This DOCX file has no headings or paragraphs to read.")

    if not title:
        # Title style / first heading-0 paragraph, else filename
        first_title_style = next((t for t, lvl in paragraphs if lvl == 0), "")
        title = first_title_style.strip()

    return finalize_book(
        title=title,
        author=author,
        source_path=str(path),
        source_format="docx",
        cover=None,
        cover_mime=None,
        drafts=drafts,
    )
