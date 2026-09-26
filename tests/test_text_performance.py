"""prepare_chapter must stay fast enough for a full-length book chapter."""
import time

from mytts.contracts import Chapter, Lang
from mytts.text.segment import prepare_chapter

_SENTENCE = (
    "В 1891 году Анна долго стояла у окна и смотрела, как снег засыпает двор, "
    "а за рекой поднимался туман; ей казалось, что тишина стоит 2500 рублей. "
)


def _synthetic_chapter(target_chars: int) -> Chapter:
    # Cyrillic text runs ~2 bytes/char in UTF-8, so this many characters is
    # roughly a 1-2 MB chapter on disk -- a stand-in for a real full chapter.
    paragraphs = []
    size = 0
    para = []
    while size < target_chars:
        para.append(_SENTENCE)
        size += len(_SENTENCE)
        if len(para) >= 8:  # a handful of sentences per paragraph, like real prose
            paragraphs.append("".join(para))
            para = []
    if para:
        paragraphs.append("".join(para))
    return Chapter(index=0, title="Глава 1. Туман", paragraphs=paragraphs)


def test_prepare_chapter_1mb_under_3_seconds():
    chapter = _synthetic_chapter(1_000_000)
    assert chapter.chars >= 1_000_000 * 0.9

    start = time.perf_counter()
    segments = prepare_chapter(chapter, Lang.ru)
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0, f"prepare_chapter took {elapsed:.2f}s for a ~1MB chapter"
    assert len(segments) > 100
