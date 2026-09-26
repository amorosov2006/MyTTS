"""MOBI / AZW / AZW3 parser: unpack with the `mobi` package, then reuse the
epub/html/pdf parser on whatever it produced. DRM-free files only."""
from __future__ import annotations

import shutil
from pathlib import Path

import mobi as _mobi

from mytts.contracts import Book, IngestError

from . import epub as _epub
from . import html as _html
from . import pdf as _pdf


def parse_book(path: Path) -> Book:
    if not path.exists() or path.stat().st_size == 0:
        raise IngestError("This file is empty.")
    try:
        tempdir, extracted = _mobi.extract(str(path))
    except Exception as e:
        message = str(e)
        if "drm" in message.lower() or "encrypt" in message.lower():
            raise IngestError("This MOBI/AZW file is DRM-protected and cannot be converted.") from e
        raise IngestError(f"This file is not a valid MOBI/AZW/AZW3 file (corrupt file): {e}") from e

    try:
        extracted_path = Path(extracted)
        suffix = extracted_path.suffix.lower()
        if suffix == ".epub":
            book = _epub.parse_book(extracted_path)
        elif suffix == ".pdf":
            book = _pdf.parse_book(extracted_path)
        else:
            book = _html.parse_book(extracted_path)
        book.source_path = str(path)
        book.source_format = "mobi"
        return book
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)
