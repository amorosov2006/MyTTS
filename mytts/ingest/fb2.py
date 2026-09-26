"""FB2 / FB2.ZIP parser: lxml, namespace-agnostic (local-name matching), recover=True."""
from __future__ import annotations

import base64
import zipfile
from pathlib import Path

from lxml import etree

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, classify_kind, finalize_book


def _local(tag) -> str:
    return etree.QName(tag).localname if isinstance(tag, str) else ""


def _children(el, *names):
    return [c for c in el if _local(c.tag) in names]


def _first_child(el, *names):
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _title_text(title_el) -> str:
    if title_el is None:
        return ""
    lines = [_text_of(p) for p in _children(title_el, "p")]
    lines = [l for l in lines if l]
    return " ".join(lines) if lines else _text_of(title_el)


def _remove_keep_tail(el) -> None:
    parent = el.getparent()
    if parent is None:
        return
    prev = el.getprevious()
    tail = el.tail or ""
    if prev is not None:
        prev.tail = (prev.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail
    parent.remove(el)


def _text_of(el) -> str:
    return " ".join(t.strip() for t in el.itertext() if t.strip())


def _paragraphs_from_section(section) -> list[str]:
    """Flatten a <section>'s content into speakable paragraphs (recursing into
    nested sub-sections, whose own titles become paragraph lines)."""
    out: list[str] = []
    for child in section:
        name = _local(child.tag)
        if name == "title":
            t = _title_text(child)
            if t:
                out.append(t)
        elif name in ("p", "subtitle"):
            t = _text_of(child)
            if t:
                out.append(t)
        elif name in ("epigraph", "cite", "annotation"):
            for p in _children(child, "p", "subtitle"):
                t = _text_of(p)
                if t:
                    out.append(t)
            for poem in _children(child, "poem"):
                out.extend(_poem_paragraphs(poem))
        elif name == "poem":
            out.extend(_poem_paragraphs(child))
        elif name == "section":
            out.extend(_paragraphs_from_section(child))
        elif name == "image":
            continue
        elif name == "empty-line":
            continue
    return out


def _poem_paragraphs(poem) -> list[str]:
    out = []
    title = _first_child(poem, "title")
    if title is not None:
        t = _title_text(title)
        if t:
            out.append(t)
    for stanza in _children(poem, "stanza"):
        lines = [_text_of(v) for v in _children(stanza, "v")]
        lines = [l for l in lines if l]
        if lines:
            out.append(", ".join(lines))
    return out


def _select_chapter_sections(body) -> list:
    """Pick the section depth that yields >1 titled chapters (spec: if a single
    top-level section wraps everything, descend into its children)."""
    level = _children(body, "section")
    for _ in range(3):
        if len(level) > 1:
            return level
        if len(level) == 1 and _children(level[0], "section"):
            level = _children(level[0], "section")
            continue
        break
    return level


def _author_name(author_el) -> str | None:
    if author_el is None:
        return None
    first = _first_child(author_el, "first-name")
    middle = _first_child(author_el, "middle-name")
    last = _first_child(author_el, "last-name")
    nick = _first_child(author_el, "nickname")
    parts = [
        (first.text or "").strip() if first is not None else "",
        (middle.text or "").strip() if middle is not None else "",
        (last.text or "").strip() if last is not None else "",
    ]
    name = " ".join(p for p in parts if p)
    if not name and nick is not None:
        name = (nick.text or "").strip()
    return name or None


def _read_root(data: bytes) -> etree._Element:
    parser = etree.XMLParser(recover=True, huge_tree=True)
    try:
        root = etree.fromstring(data, parser=parser)
    except Exception:
        root = None
    if root is None:
        raise IngestError("This file is not a valid FB2 (corrupt or unreadable XML).")
    return root


def _parse_fb2_bytes(data: bytes, source_path: str) -> Book:
    root = _read_root(data)

    description = _first_child(root, "description")
    title_info = _first_child(description, "title-info") if description is not None else None

    title = ""
    author = None
    if title_info is not None:
        book_title_el = _first_child(title_info, "book-title")
        if book_title_el is not None:
            title = (book_title_el.text or "").strip()
        author = _author_name(_first_child(title_info, "author"))

    bodies = _children(root, "body")
    if not bodies:
        raise IngestError("This FB2 file has no <body> content.")

    main_body = next((b for b in bodies if b.get("name") is None), bodies[0])
    other_bodies = [b for b in bodies if b is not main_body]

    # cover image
    cover, cover_mime = None, None
    coverpage = _first_child(main_body, "coverpage")
    if coverpage is not None:
        image = _first_child(coverpage, "image")
        if image is not None:
            href = None
            for attr, val in image.attrib.items():
                if attr.endswith("href"):
                    href = val.lstrip("#")
            if href:
                for binary in root.iter():
                    if _local(binary.tag) == "binary" and binary.get("id") == href:
                        try:
                            cover = base64.b64decode("".join(binary.itertext()).strip())
                        except Exception:
                            cover = None
                        cover_mime = binary.get("content-type") or "image/jpeg"

    for a in list(main_body.iter()):
        if _local(a.tag) == "a" and a.get("type") == "note":
            _remove_keep_tail(a)

    sections = _select_chapter_sections(main_body)
    drafts: list[ChapterDraft] = []
    for sec in sections:
        title_el = _first_child(sec, "title")
        chapter_title = _title_text(title_el) if title_el is not None else ""
        paragraphs = [p for p in _paragraphs_from_section(sec)]
        if title_el is not None and paragraphs and paragraphs[0] == chapter_title:
            paragraphs = paragraphs[1:]  # drop duplicate title-as-paragraph
        drafts.append(ChapterDraft(chapter_title or "", paragraphs, "body", True))

    for body in other_bodies:
        name = body.get("name") or ""
        for sec in _children(body, "section"):
            title_el = _first_child(sec, "title")
            chapter_title = _title_text(title_el) if title_el is not None else name
            paragraphs = _paragraphs_from_section(sec)
            if title_el is not None and paragraphs and paragraphs[0] == chapter_title:
                paragraphs = paragraphs[1:]
            kind = "notes" if name == "notes" else classify_kind(chapter_title)
            if kind == "body":
                kind = "back"
            drafts.append(ChapterDraft(chapter_title or name, paragraphs, kind, False))

    if not drafts:
        raise IngestError("This FB2 file has no sections to read.")

    return finalize_book(
        title=title,
        author=author,
        source_path=source_path,
        source_format="fb2",
        cover=cover,
        cover_mime=cover_mime,
        drafts=drafts,
    )


def parse_book(path: Path) -> Book:
    data = path.read_bytes()
    if not data.strip():
        raise IngestError("This file is empty.")
    return _parse_fb2_bytes(data, str(path))


def parse_book_zip(path: Path) -> Book:
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".fb2")]
            if not names:
                raise IngestError("This .fb2.zip archive contains no .fb2 file.")
            data = z.read(names[0])
    except zipfile.BadZipFile as e:
        raise IngestError("This file is not a valid .fb2.zip archive (corrupt zip).") from e
    return _parse_fb2_bytes(data, str(path))
