"""Plain text parser: charset detection, blank-line paragraphs, heading detection."""
from __future__ import annotations

import re
from pathlib import Path

from charset_normalizer import from_bytes

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, finalize_book, looks_like_heading

_BLOCK_SPLIT_RE = re.compile(r"\n[ \t]*\n+")


def _decode(data: bytes) -> str:
    if not data.strip():
        raise IngestError("This file is empty.")
    for bom, enc in ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"), (b"\xef\xbb\xbf", "utf-8-sig")):
        if data.startswith(bom):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                break
    matches = from_bytes(data)
    best = matches.best()
    if best is None:
        raise IngestError("Could not detect the text encoding of this file.")
    return str(best)


def _rewrap(block_lines: list[str]) -> str:
    """Join a hard-wrapped paragraph's lines into one line."""
    return re.sub(r"\s+", " ", " ".join(block_lines)).strip()


def parse_book(path: Path) -> Book:
    data = path.read_bytes()
    text = _decode(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    blocks = [b for b in _BLOCK_SPLIT_RE.split(text.strip()) if b.strip()]
    if not blocks:
        raise IngestError("This file is empty.")

    title = ""
    author = None
    body_blocks = blocks
    first_block_lines = [l for l in blocks[0].splitlines() if l.strip()]
    if len(blocks) > 1 and 1 <= len(first_block_lines) <= 2 and all(
        len(l) < 100 and not looks_like_heading(l) for l in first_block_lines
    ):
        title = first_block_lines[0].strip()
        if len(first_block_lines) == 2:
            author = first_block_lines[1].strip()
        body_blocks = blocks[1:]

    drafts: list[ChapterDraft] = []
    current_title = ""
    current_paras: list[str] = []
    started = False

    def flush():
        if started:
            drafts.append(ChapterDraft(current_title, list(current_paras), "body", True))

    any_heading = False
    for block in body_blocks:
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) == 1 and looks_like_heading(lines[0]):
            flush()
            current_title = lines[0].strip()
            current_paras = []
            started = True
            any_heading = True
        else:
            para = _rewrap(lines)
            if not started:
                started = True
                current_title = ""
            if para:
                current_paras.append(para)
    flush()

    if not any_heading:
        # no headings anywhere: one chapter titled by the book title
        all_paras = [p for d in drafts for p in d.paragraphs]
        drafts = [ChapterDraft(title, all_paras, "body", True)]

    if not drafts:
        raise IngestError("This file has no readable text.")

    return finalize_book(
        title=title,
        author=author,
        source_path=str(path),
        source_format="txt",
        cover=None,
        cover_mime=None,
        drafts=drafts,
    )
