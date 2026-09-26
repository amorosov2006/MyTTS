"""Dependency-injection point for the modules other agents build.

`Scheduler` and `mytts.voices` take a `Services` instance instead of importing
`mytts.ingest` / `mytts.text` / `mytts.audio.post` / `mytts.pipeline.memguard`
directly. Defaults resolve those imports lazily (only when a `Services()` is
constructed with no override), so this module — and anything that only
type-checks against it — imports cleanly even before those modules exist.
Tests inject fakes for every field instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from mytts.contracts import Book, Chapter, Lang, Segment


def _lazy_ingest_parse_book():
    from mytts.ingest import parse_book
    return parse_book


def _lazy_text_prepare_chapter():
    from mytts.text import prepare_chapter
    return prepare_chapter


def _lazy_text_detect_lang():
    from mytts.text import detect_lang
    return detect_lang


def _lazy_post_process_segment():
    from mytts.audio.post import process_segment
    return process_segment


def _lazy_post_assemble():
    from mytts.audio.post import assemble
    return assemble


def _lazy_post_build_m4b():
    from mytts.audio.post import build_m4b
    return build_m4b


def _lazy_memguard_footprint():
    from mytts.pipeline import memguard
    return memguard.footprint


def _lazy_memguard_available_bytes():
    from mytts.pipeline import memguard
    return memguard.available_bytes


def _lazy_memguard_total_bytes():
    from mytts.pipeline import memguard
    return memguard.total_bytes


# Type aliases documenting the contracted signatures (see mytts/contracts.py and the
# module docstrings in mytts/ingest, mytts/text, mytts/audio/post, mytts/pipeline/memguard).
ParseBookFn = Callable[[Path], Book]
PrepareChapterFn = Callable[..., list[Segment]]
DetectLangFn = Callable[[str], Lang]
ProcessSegmentFn = Callable[..., float]
AssembleFn = Callable[..., float]
BuildM4bFn = Callable[..., float]
FootprintFn = Callable[[int], float]
AvailableBytesFn = Callable[[], int]
TotalBytesFn = Callable[[], int]


@dataclass
class Services:
    """Every external dependency the pipeline needs, injectable for tests."""

    parse_book: Optional[ParseBookFn] = None
    prepare_chapter: Optional[PrepareChapterFn] = None
    detect_lang: Optional[DetectLangFn] = None
    process_segment: Optional[ProcessSegmentFn] = None
    assemble: Optional[AssembleFn] = None
    build_m4b: Optional[BuildM4bFn] = None
    footprint: Optional[FootprintFn] = None
    available_bytes: Optional[AvailableBytesFn] = None
    total_bytes: Optional[TotalBytesFn] = None

    def __post_init__(self) -> None:
        if self.parse_book is None:
            self.parse_book = _lazy_ingest_parse_book()
        if self.prepare_chapter is None:
            self.prepare_chapter = _lazy_text_prepare_chapter()
        if self.detect_lang is None:
            self.detect_lang = _lazy_text_detect_lang()
        if self.process_segment is None:
            self.process_segment = _lazy_post_process_segment()
        if self.assemble is None:
            self.assemble = _lazy_post_assemble()
        if self.build_m4b is None:
            self.build_m4b = _lazy_post_build_m4b()
        if self.footprint is None:
            self.footprint = _lazy_memguard_footprint()
        if self.available_bytes is None:
            self.available_bytes = _lazy_memguard_available_bytes()
        if self.total_bytes is None:
            self.total_bytes = _lazy_memguard_total_bytes()
