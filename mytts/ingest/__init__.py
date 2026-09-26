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

MAX_PLAIN_FILE_BYTES = 200 * 1024 * 1024
MAX_ZIP_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024
MAX_ZIP_MEMBER_UNCOMPRESSED = 300 * 1024 * 1024
MAX_ZIP_MEMBERS = 10_000
MAX_ZIP_COMPRESSION_RATIO = 100
_ZIP_RATIO_MIN_ARCHIVE_BYTES = 1024 * 1024


def _check_zip_safety(path: Path) -> None:
    """Reject zip bombs before any member is decompressed: too many entries, too
    much (or too concentrated) uncompressed data, or a suspicious ratio against the
    archive's actual size on disk (which, unlike header fields, can't be forged)."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
    except zipfile.BadZipFile:
        return  # let the format-specific parser raise a clearer "corrupt archive" error
    if len(infos) > MAX_ZIP_MEMBERS:
        raise IngestError("This archive has too many entries to be a real book.")
    total_uncompressed = sum(i.file_size for i in infos)
    if total_uncompressed > MAX_ZIP_TOTAL_UNCOMPRESSED:
        raise IngestError("This archive is too large once decompressed.")
    if any(i.file_size > MAX_ZIP_MEMBER_UNCOMPRESSED for i in infos):
        raise IngestError("This archive contains an entry that is too large.")
    archive_size = path.stat().st_size
    ratio = total_uncompressed / archive_size if archive_size else 0
    if archive_size > _ZIP_RATIO_MIN_ARCHIVE_BYTES and ratio > MAX_ZIP_COMPRESSION_RATIO:
        raise IngestError("This archive's compression ratio looks like a zip bomb.")


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
    size = path.stat().st_size
    if size == 0:
        raise IngestError("This file is empty.")

    if zipfile.is_zipfile(path):
        _check_zip_safety(path)
    elif size > MAX_PLAIN_FILE_BYTES:
        raise IngestError("This file is too large to convert.")

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
