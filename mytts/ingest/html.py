"""HTML parser, also the target format for rtf/odt/doc (converted via macOS `textutil`).

Real HTML uses h1/h2/h3. Documents round-tripped through `textutil` lose heading
tags entirely (chapter titles become plain, fully-bold paragraphs), so there is a
bold-paragraph fallback for that case.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bs4 import BeautifulSoup
from charset_normalizer import from_bytes

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, classify_kind, finalize_book

_BLOCK_TAGS = ("p", "div", "li", "blockquote")
_HEADING_TAGS = ("h1", "h2", "h3", "h4")
_AUTHOR_LINE_RE = re.compile(r"^(by|автор|author)\s*[:\s]\s*(.+)$", re.IGNORECASE)


def _decode_html(data: bytes) -> str:
    m = re.search(rb'charset=["\']?([\w-]+)', data[:2000], re.IGNORECASE)
    if m:
        try:
            return data.decode(m.group(1).decode("ascii"), errors="strict")
        except (LookupError, UnicodeDecodeError):
            pass
    best = from_bytes(data).best()
    return str(best) if best is not None else data.decode("utf-8", errors="replace")


def _is_block_leaf(el) -> bool:
    return el.find(_BLOCK_TAGS) is None


def _fully_bold(el) -> str | None:
    """If `el`'s entire text comes from <b>/<strong> children, return that text."""
    text = el.get_text(" ", strip=True)
    if not text or len(text) > 150:
        return None
    bold_text = " ".join(b.get_text(" ", strip=True) for b in el.find_all(["b", "strong"]))
    if not bold_text:
        return None
    if re.sub(r"\s+", "", bold_text) == re.sub(r"\s+", "", text):
        return text
    return None


def _parse_soup(soup: BeautifulSoup, source_path: str, source_format: str) -> Book:
    for tag in soup.find_all(["script", "style", "nav"]):
        tag.decompose()

    body = soup.body or soup
    title_tag = soup.find("title")
    doc_title = title_tag.get_text(strip=True) if title_tag else ""

    headings = {lvl: soup.find_all(f"h{lvl}") for lvl in (1, 2, 3, 4)}
    h1 = headings[1]

    use_bold_fallback = not any(headings.values())

    if not use_bold_fallback:
        if len(h1) == 1:
            title = doc_title or h1[0].get_text(" ", strip=True)
            split_level = 2 if headings[2] else (3 if headings[3] else 1)
        elif len(h1) > 1:
            title = doc_title
            split_level = 1
        else:
            split_level = next((lvl for lvl in (2, 3, 4) if headings[lvl]), None)
            title = doc_title
        split_tags = {f"h{split_level}"} if split_level else set()
        title_tag_els = set(h1) if (len(h1) == 1 and split_level != 1) else set()
    else:
        title = doc_title
        split_tags = set()
        title_tag_els = set()

    blocks = [el for el in body.find_all(list(_HEADING_TAGS) + list(_BLOCK_TAGS)) if _is_block_leaf(el)]

    drafts: list[ChapterDraft] = []
    lead: list[str] = []
    current_title = ""
    current_paras: list[str] = []
    started = False

    def flush():
        if started:
            drafts.append(ChapterDraft(current_title, list(current_paras), "body", True))

    author = None
    for el in blocks:
        if el in title_tag_els:
            continue
        heading_text = None
        if el.name in split_tags:
            heading_text = el.get_text(" ", strip=True)
        elif use_bold_fallback:
            bold = _fully_bold(el)
            if bold is not None:
                if not title:
                    title = bold
                    continue  # first bold line with no other title = the book title
                heading_text = bold
        if heading_text is not None:
            flush()
            current_title = heading_text
            current_paras = []
            started = True
            continue
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if not started:
            m = _AUTHOR_LINE_RE.match(text)
            if m and author is None:
                author = m.group(2).strip()
            else:
                lead.append(text)
            continue
        current_paras.append(text)
    flush()

    if lead:
        drafts.insert(0, ChapterDraft(title or "Front matter", lead, "front", False))

    for d in drafts:
        if d.kind == "body":
            k = classify_kind(d.title)
            if k != "body":
                d.kind = k
                d.include = False

    if not drafts:
        raise IngestError("This document has no headings or paragraphs to read.")

    return finalize_book(
        title=title,
        author=author,
        source_path=source_path,
        source_format=source_format,
        cover=None,
        cover_mime=None,
        drafts=drafts,
    )


def parse_book(path: Path) -> Book:
    data = path.read_bytes()
    if not data.strip():
        raise IngestError("This file is empty.")
    text = _decode_html(data)
    soup = BeautifulSoup(text, "lxml")
    return _parse_soup(soup, str(path), "html")


def parse_via_textutil(path: Path, fmt: str) -> Book:
    try:
        result = subprocess.run(
            ["textutil", "-convert", "html", "-stdout", "-encoding", "UTF-8", str(path)],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise IngestError(f"Could not convert this .{fmt} file (textutil unavailable or timed out).") from e
    if result.returncode != 0 or not result.stdout.strip():
        raise IngestError(f"This .{fmt} file could not be read (corrupt or unsupported file).")
    soup = BeautifulSoup(result.stdout.decode("utf-8", errors="replace"), "lxml")
    return _parse_soup(soup, str(path), fmt)
