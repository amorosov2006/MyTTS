"""Markdown parser: markdown-it tokens, # / ## split chapters, code blocks dropped."""
from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, finalize_book


def _inline_text(children) -> str:
    out = []
    for c in children or []:
        if c.type == "image":
            continue
        if c.type in ("text", "code_inline"):
            out.append(c.content)
        elif c.type == "softbreak":
            out.append(" ")
    return "".join(out).strip()


def parse_book(path: Path) -> Book:
    raw = path.read_bytes()
    if not raw.strip():
        raise IngestError("This file is empty.")
    text = raw.decode("utf-8", errors="replace")

    tokens = MarkdownIt().parse(text)

    h1_texts = [
        _inline_text(tokens[i + 1].children)
        for i, t in enumerate(tokens)
        if t.type == "heading_open" and t.tag == "h1"
    ]
    title = ""
    split_tag = "h1"
    if len(h1_texts) == 1:
        title = h1_texts[0]
        split_tag = "h2"

    drafts: list[ChapterDraft] = []
    current_title = ""
    current_paras: list[str] = []
    started = False

    def flush():
        if started:
            drafts.append(ChapterDraft(current_title, list(current_paras), "body", True))

    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            heading_text = _inline_text(tokens[i + 1].children)
            if t.tag == split_tag:
                flush()
                current_title = heading_text
                current_paras = []
                started = True
            # else: e.g. the single H1 used as the book title -> skip, not a chapter
            i += 3
            continue
        if t.type == "paragraph_open":
            para = _inline_text(tokens[i + 1].children)
            if started and para:
                current_paras.append(para)
            i += 3
            continue
        i += 1
    flush()

    if not drafts:
        raise IngestError("This Markdown file has no headings or paragraphs to read.")

    return finalize_book(
        title=title,
        author=None,
        source_path=str(path),
        source_format="md",
        cover=None,
        cover_mime=None,
        drafts=drafts,
    )
