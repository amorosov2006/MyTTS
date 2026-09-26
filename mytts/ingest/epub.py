"""EPUB parser: spine order, TOC-driven chapter split, footnote stripping."""
from __future__ import annotations

import re
import warnings
import zipfile
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from ebooklib import epub

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, classify_kind, finalize_book

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_FOOTNOTE_TYPE_RE = re.compile(r"\b(footnote|endnote|rearnote)\b", re.IGNORECASE)
_HEADING_TAGS = ("h1", "h2", "h3", "h4")


def _check_drm(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "META-INF/encryption.xml" in names:
                data = z.read("META-INF/encryption.xml")
                if b"EncryptedData" in data or b"EncryptionMethod" in data:
                    raise IngestError("This EPUB is DRM-protected and cannot be converted.")
    except zipfile.BadZipFile as e:
        raise IngestError("This file is not a valid EPUB (corrupt archive).") from e


def _strip_notes(soup: BeautifulSoup) -> None:
    for tag in list(soup.find_all(attrs={"epub:type": _FOOTNOTE_TYPE_RE})):
        tag.decompose()
    for tag in list(soup.select('[role="doc-footnote"], [role="doc-endnote"]')):
        tag.decompose()
    for tag in list(soup.find_all("a", attrs={"epub:type": re.compile(r"noteref", re.I)})):
        tag.decompose()
    for tag in list(soup.find_all("sup")):
        text = tag.get_text(strip=True)
        links = tag.find_all("a")
        if text.isdigit() or (len(links) == 1 and links[0].get_text(strip=True) == text):
            tag.decompose()


def _extract_paragraphs(body) -> list[str]:
    out = []
    for el in body.find_all(["p", "div", "li"], recursive=True):
        # skip containers that themselves contain block children (avoid duplicate text)
        if el.find(["p", "div", "li"]):
            continue
        text = el.get_text(" ", strip=True)
        if text:
            out.append(text)
    if not out:
        text = body.get_text(" ", strip=True)
        if text:
            out.append(text)
    return out


def _first_heading(soup: BeautifulSoup) -> str:
    for tag_name in _HEADING_TAGS:
        tag = soup.find(tag_name)
        if tag:
            text = tag.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _find_cover(book: epub.EpubBook) -> tuple[bytes | None, str | None]:
    cover_ids = set()
    for meta in book.get_metadata("OPF", "cover"):
        content = meta[1].get("content") if len(meta) > 1 else None
        if content:
            cover_ids.add(content)

    images = [it for it in book.get_items() if isinstance(it, epub.EpubImage)]
    for it in images:
        props = getattr(it, "properties", None) or []
        if it.get_id() in cover_ids or "cover-image" in props:
            return it.get_content(), it.media_type
    for it in images:
        name = (it.get_name() or "").lower()
        if "cover" in name or "cover" in it.get_id().lower():
            return it.get_content(), it.media_type
    if len(images) == 1:
        return images[0].get_content(), images[0].media_type
    return None, None


def parse_book(path: Path) -> Book:
    _check_drm(path)
    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": False})
    except Exception as e:
        raise IngestError(f"This EPUB file could not be read (corrupt file): {e}") from e

    titles = book.get_metadata("DC", "title")
    title = titles[0][0] if titles else ""
    creators = book.get_metadata("DC", "creator")
    author = creators[0][0] if creators else None

    cover, cover_mime = _find_cover(book)

    # TOC hrefs (without in-doc anchors) in order -> which spine docs are "chapters"
    toc_hrefs: list[tuple[str, str, str]] = []  # (href_no_anchor, anchor, label)

    def walk_toc(nodes):
        for node in nodes:
            if isinstance(node, tuple):  # (Section/Link, children)
                head, children = node
                if hasattr(head, "href"):
                    walk_toc([head])
                walk_toc(children)
            elif hasattr(node, "href") and node.href:
                href = unquote(node.href)
                base, _, anchor = href.partition("#")
                toc_hrefs.append((base, anchor, (node.title or "").strip()))

    walk_toc(book.toc)

    toc_href_set = {h for h, _, _ in toc_hrefs}

    drafts: list[ChapterDraft] = []
    seen_toc_doc = False
    for idref, _linear in book.spine:
        item = book.get_item_with_id(idref)
        if item is None or isinstance(item, epub.EpubNav) or not isinstance(item, epub.EpubHtml):
            continue
        name = unquote(item.get_name())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
            soup = BeautifulSoup(item.get_content(), "lxml")
        _strip_notes(soup)
        body = soup.body or soup

        in_toc = name in toc_href_set
        heading = _first_heading(soup)
        label = next((lbl for h, _, lbl in toc_hrefs if h == name and lbl), "")
        chapter_title = label or heading

        if in_toc:
            seen_toc_doc = True
            kind = "body"
            include = True
        else:
            kind = classify_kind(chapter_title)
            if kind == "body":  # no keyword hit: guess by position relative to the TOC
                kind = "front" if not seen_toc_doc else "back"
            include = False

        paragraphs = _extract_paragraphs(body)
        drafts.append(ChapterDraft(chapter_title or Path(name).stem, paragraphs, kind, include))

    if not drafts:
        raise IngestError("This EPUB has no readable content documents.")

    return finalize_book(
        title=title,
        author=author,
        source_path=str(path),
        source_format="epub",
        cover=cover,
        cover_mime=cover_mime,
        drafts=drafts,
    )
