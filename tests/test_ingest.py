"""Ingest parser tests, parametrized over every fixture book in every format
declared in tests/fixtures/books/expected.json."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from mytts.contracts import IngestError, Lang
from mytts.ingest import parse_book

FIXTURES = Path(__file__).parent / "fixtures" / "books"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8"))
SLUGS = list(EXPECTED.keys())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _norm_ci(s: str) -> str:
    return _norm(s).casefold()


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w+", s.lower()))


def _body_text(book) -> str:
    return _norm(" ".join(p for c in book.chapters if c.include for p in c.paragraphs))


def _all_text(book) -> str:
    return _norm(" ".join(p for c in book.chapters for p in c.paragraphs))


FORMAT_CASES = [
    (slug, fmt, tuple(checks))
    for slug, meta in EXPECTED.items()
    for fmt, checks in meta["formats"].items()
]


@pytest.mark.parametrize(
    "slug,fmt,checks", FORMAT_CASES, ids=[f"{s}-{f}" for s, f, _ in FORMAT_CASES]
)
def test_format(slug, fmt, checks):
    meta = EXPECTED[slug]
    book = parse_book(FIXTURES / f"{slug}.{fmt}")
    body_chapters = [c for c in book.chapters if c.include]

    if "lang" in checks:
        assert book.lang == Lang(meta["lang"])

    if "title" in checks:
        assert _norm_ci(book.title) == _norm_ci(meta["title"])

    if "author" in checks:
        assert book.author is not None
        assert _norm_ci(book.author) == _norm_ci(meta["author"])

    if "chapters" in checks:
        assert len(body_chapters) == 3, f"expected 3 body chapters, got {[c.title for c in body_chapters]}"
        for ch, expected_title in zip(body_chapters, meta["chapter_titles"]):
            assert _norm_ci(ch.title) == _norm_ci(expected_title)
        for ch, sentence in zip(body_chapters, meta["key_sentences"]):
            assert _norm(sentence) in _norm(" ".join(ch.paragraphs))

    if "footnote" in checks:
        assert _norm(meta["footnote_text"]) not in _body_text(book)


@pytest.mark.parametrize("slug", SLUGS)
@pytest.mark.parametrize("fmt", ["epub", "fb2"])
def test_cover_present(slug, fmt):
    book = parse_book(FIXTURES / f"{slug}.{fmt}")
    assert book.cover, "expected a cover image"
    assert len(book.cover) > 100
    assert book.cover_mime


@pytest.mark.parametrize("slug", SLUGS)
def test_pdf_no_header_footer_junk_and_dehyphenated(slug):
    meta = EXPECTED[slug]
    book = parse_book(FIXTURES / f"{slug}.pdf")
    full_text = _all_text(book)

    # the footnote sentence must not survive into any chapter's body
    assert _norm(meta["footnote_text"]) not in full_text

    # the running header (== book title) must not appear as body content
    all_paragraphs = [p.strip() for c in book.chapters for p in c.paragraphs]
    assert meta["title"] not in all_paragraphs

    # no bare page-number paragraph survived
    assert not any(re.fullmatch(r"\d{1,3}", p.strip()) for p in all_paragraphs)

    # de-hyphenated words appear whole, not split across a line break
    whole_words = ["особенно", "разбросанных", "путешествовал"] if slug.endswith("_ru") else [
        "difficult",
        "uncertain",
    ]
    for word in whole_words:
        assert word in full_text, f"{word!r} missing or still hyphen-split in {full_text!r}"


def test_txt_cp1251_matches_utf8():
    utf8 = parse_book(FIXTURES / "quiet_station_ru.txt")
    cp1251 = parse_book(FIXTURES / "quiet_station_ru_cp1251.txt")
    assert utf8.title == cp1251.title
    assert utf8.author == cp1251.author
    assert [c.paragraphs for c in utf8.chapters] == [c.paragraphs for c in cp1251.chapters]


def test_txt_wrapped_matches_unwrapped():
    flat = parse_book(FIXTURES / "quiet_station_ru.txt")
    wrapped = parse_book(FIXTURES / "quiet_station_ru_wrapped.txt")
    assert [c.paragraphs for c in flat.chapters] == [c.paragraphs for c in wrapped.chapters]


@pytest.mark.parametrize("slug", SLUGS)
def test_fb2_zip_matches_fb2(slug):
    plain = parse_book(FIXTURES / f"{slug}.fb2")
    zipped = parse_book(FIXTURES / f"{slug}.fb2.zip")
    assert plain.title == zipped.title
    assert [(c.title, c.paragraphs, c.include) for c in plain.chapters] == [
        (c.title, c.paragraphs, c.include) for c in zipped.chapters
    ]


@pytest.mark.parametrize("slug", SLUGS)
def test_scanned_pdf_ocr(slug):
    meta = EXPECTED[slug]
    book = parse_book(FIXTURES / f"{slug}_scanned.pdf")
    assert any("OCR" in w for w in book.warnings)

    got_words = _tokens(_body_text(book))
    expected_words = _tokens(meta["key_sentences"][0])
    overlap = expected_words & got_words
    assert len(overlap) / len(expected_words) >= 0.8, (expected_words, got_words)


@pytest.mark.parametrize("fmt", ["rtf", "odt", "doc"])
def test_textutil_formats(fmt):
    meta = EXPECTED["quiet_station_ru"]
    book = parse_book(FIXTURES / f"quiet_station_ru.{fmt}")
    body_chapters = [c for c in book.chapters if c.include]
    assert len(body_chapters) == 3
    for ch, expected_title in zip(body_chapters, meta["chapter_titles"]):
        assert _norm_ci(ch.title) == _norm_ci(expected_title)


def test_error_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_bytes(b"")
    with pytest.raises(IngestError):
        parse_book(p)


def test_error_binary_garbage_epub(tmp_path):
    p = tmp_path / "garbage.epub"
    p.write_bytes(os.urandom(2000))
    with pytest.raises(IngestError):
        parse_book(p)


def test_error_unknown_extension(tmp_path):
    p = tmp_path / "book.xyz"
    p.write_text("hello world", encoding="utf-8")
    with pytest.raises(IngestError):
        parse_book(p)


def test_error_fake_mobi(tmp_path):
    p = tmp_path / "fake.mobi"
    p.write_bytes(b"not a real mobi file" * 20)
    with pytest.raises(IngestError):
        parse_book(p)


def test_txt_performance(tmp_path):
    para = "Это тестовый абзац для проверки скорости разбора текста. " * 8
    chunks = ["Большая книга\nАвтор Тестов\n\n"]
    size = len(chunks[0].encode("utf-8"))
    i = 0
    while size < 2 * 1024 * 1024:
        i += 1
        block = f"\nГлава {i}. Заголовок\n\n" + "\n\n".join([para] * 10) + "\n"
        chunks.append(block)
        size += len(block.encode("utf-8"))
    data = "".join(chunks).encode("utf-8")

    p = tmp_path / "big.txt"
    p.write_bytes(data)

    start = time.time()
    book = parse_book(p)
    elapsed = time.time() - start

    assert elapsed < 3.0, f"parsing a 2MB txt took {elapsed:.2f}s"
    assert len(book.chapters) > 1
