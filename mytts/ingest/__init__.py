"""Book ingest: parse a source file of any supported format into a `Book`.

Dispatch is by (lowercased) file extension, multi-suffix aware for ".fb2.zip".
Formats sharing an underlying converter (rtf/odt/doc -> html via macOS `textutil`)
are routed to the same module.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from mytts.contracts import Book, IngestError

from . import docx as _docx
from . import epub as _epub
from . import fb2 as _fb2
from . import html as _html
from . import md as _md
from . import mobi as _mobi
from . import pdf as _pdf
from . import txt as _txt

SUPPORTED_EXTENSIONS = [
    ".epub",
    ".fb2",
    ".fb2.zip",
    ".docx",
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".rtf",
    ".odt",
    ".doc",
    ".mobi",
    ".azw",
    ".azw3",
]

_TEXTUTIL_FORMATS = {".rtf": "rtf", ".odt": "odt", ".doc": "doc"}


def _match_extension(path: Path) -> str | None:
    name = path.name.lower()
    for ext in sorted(SUPPORTED_EXTENSIONS, key=len, reverse=True):
        if name.endswith(ext):
            return ext
    return None


def _sniff_zip_based(path: Path) -> str | None:
    """A misnamed epub/docx/odt is still a zip archive; peek inside to recover
    its real format cheaply."""
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return None
    if "mimetype" in names or "META-INF/container.xml" in names:
        return ".epub"
    if "word/document.xml" in names:
        return ".docx"
    if "content.xml" in names and "META-INF/manifest.xml" in names:
        return ".odt"
    if any(n.lower().endswith(".fb2") for n in names):
        return ".fb2.zip"
    return None


def parse_book(path: Path) -> Book:
    path = Path(path)
    if not path.exists():
        raise IngestError(f"File not found: {path}")
    if path.stat().st_size == 0:
        raise IngestError("This file is empty.")

    ext = _match_extension(path)
    if ext is None:
        sniffed = _sniff_zip_based(path)
        if sniffed is None:
            raise IngestError(f"Unsupported file format: {path.suffix or '(no extension)'}")
        ext = sniffed

    if ext == ".epub":
        return _epub.parse_book(path)
    if ext == ".fb2.zip":
        return _fb2.parse_book_zip(path)
    if ext == ".fb2":
        return _fb2.parse_book(path)
    if ext == ".docx":
        return _docx.parse_book(path)
    if ext == ".pdf":
        return _pdf.parse_book(path)
    if ext == ".txt":
        return _txt.parse_book(path)
    if ext in (".md", ".markdown"):
        return _md.parse_book(path)
    if ext in (".html", ".htm"):
        return _html.parse_book(path)
    if ext in _TEXTUTIL_FORMATS:
        return _html.parse_via_textutil(path, _TEXTUTIL_FORMATS[ext])
    if ext in (".mobi", ".azw", ".azw3"):
        return _mobi.parse_book(path)

    raise IngestError(f"Unsupported file format: {ext}")
