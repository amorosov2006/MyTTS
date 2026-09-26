"""prepare_chapter invariants: length caps, pauses, ids, footnotes, mixed language."""
import re

import pytest

from mytts import config
from mytts.contracts import Chapter, Lang
from mytts.text.segment import is_scene_separator, normalize_title, prepare_chapter, strip_footnotes

# ----------------------------------------------------------------------------- footnotes

@pytest.mark.parametrize("text,expected", [
    ("Слово[12] и текст{3} со звездой*.", "Слово и текст со звездой."),
    ("Superscript footnote¹² marker here.", "Superscript footnote marker here."),
    ("[*] в начале строки.", "в начале строки."),
    ("Обычный текст без сносок.", "Обычный текст без сносок."),
    ("Слово с прикреплённой(1) сноской.", "Слово с прикреплённой сноской."),
    ("Сноска с пробелом (1) не трогается.", "Сноска с пробелом (1) не трогается."),
])
def test_strip_footnotes(text, expected):
    assert strip_footnotes(text) == expected


# ----------------------------------------------------------------------------- scene separators

@pytest.mark.parametrize("text,expected", [
    ("* * *", True),
    ("***", True),
    ("⁂", True),
    ("   ***   ", True),
    ("Обычный текст.", False),
    ("", False),
])
def test_is_scene_separator(text, expected):
    assert is_scene_separator(text) == expected


# ----------------------------------------------------------------------------- titles

@pytest.mark.parametrize("title,lang,expected", [
    ("Глава 1. Туман", Lang.ru, "Глава первая. Туман"),
    ("Глава IV", Lang.ru, "Глава четвёртая"),
    ("Часть 3", Lang.ru, "Часть третья"),
    ("Chapter 4", Lang.en, "Chapter four"),
    ("Chapter IV", Lang.en, "Chapter four"),
    ("IV", Lang.en, "four"),
    ("IV", Lang.ru, "четыре"),
    ("", Lang.ru, ""),
])
def test_normalize_title(title, lang, expected):
    assert normalize_title(title, lang) == expected


def _make_chapter(title="", paragraphs=None, index=1):
    return Chapter(index=index, title=title, paragraphs=paragraphs or [])


def test_title_segment_created_when_requested():
    ch = _make_chapter(title="Глава 1. Туман", paragraphs=["Текст главы."])
    segs = prepare_chapter(ch, Lang.ru, read_title=True)
    assert segs[0].is_title
    assert segs[0].pause_after_ms == config.PAUSE_CHAPTER_TITLE_MS
    assert segs[0].source == "Глава 1. Туман"
    assert segs[0].index == 0


def test_title_segment_skipped_when_not_requested():
    ch = _make_chapter(title="Глава 1. Туман", paragraphs=["Текст главы."])
    segs = prepare_chapter(ch, Lang.ru, read_title=False)
    assert not any(s.is_title for s in segs)


def test_title_segment_skipped_when_empty():
    ch = _make_chapter(title="", paragraphs=["Текст главы."])
    segs = prepare_chapter(ch, Lang.ru, read_title=True)
    assert not any(s.is_title for s in segs)


# ----------------------------------------------------------------------------- pauses / scene breaks

def test_scene_separator_no_segment_boosts_previous_pause():
    ch = _make_chapter(paragraphs=["Первый абзац.", "* * *", "Второй абзац."])
    segs = prepare_chapter(ch, Lang.ru, pause_paragraph_ms=700)
    texts = [s.text for s in segs]
    assert "Первый абзац." in texts[0]
    assert "Второй абзац." in texts[-1]
    assert len(segs) == 2  # the separator itself produced no segment
    assert segs[0].pause_after_ms == 1400  # 700 * 2


def test_pause_sentence_inside_paragraph_and_paragraph_at_end():
    long_para = "Первое предложение здесь. Второе предложение здесь. Третье предложение здесь."
    ch = _make_chapter(paragraphs=[long_para])
    segs = prepare_chapter(ch, Lang.ru, pause_sentence_ms=250, pause_paragraph_ms=700)
    assert segs[-1].pause_after_ms == 700
    for s in segs[:-1]:
        assert s.pause_after_ms == 250


# ----------------------------------------------------------------------------- ids

def test_segment_ids_are_stable_and_sortable():
    ch = _make_chapter(index=7, paragraphs=["Раз. Два. Три.", "Четыре. Пять."])
    segs = prepare_chapter(ch, Lang.ru)
    ids = [s.id for s in segs]
    assert ids == sorted(ids)
    assert all(re.fullmatch(r"c007s\d{4}", i) for i in ids)
    assert ids == [f"c007s{i:04d}" for i in range(len(segs))]


# ----------------------------------------------------------------------------- length / no paragraph crossing

def test_segments_never_exceed_max_chars():
    long_sentence = "Это очень длинное предложение, которое содержит много слов и оборотов, " * 6 + "и заканчивается тут."
    ch = _make_chapter(paragraphs=[long_sentence])
    segs = prepare_chapter(ch, Lang.ru)
    for s in segs:
        assert len(s.text) <= config.SEGMENT_MAX_CHARS


def test_no_segment_crosses_a_paragraph_boundary():
    paragraphs = ["Абзац один. Ещё одно предложение один.", "Абзац два. Ещё одно предложение два."]
    ch = _make_chapter(paragraphs=paragraphs)
    segs = prepare_chapter(ch, Lang.ru)
    for s in segs:
        # every segment's source must be fully contained in exactly one paragraph
        owners = [p for p in paragraphs if s.source in p]
        assert len(owners) == 1, s.source


def test_tiny_fragments_get_merged():
    ch = _make_chapter(paragraphs=["Да. Нет. Это было очень длинное и содержательное предложение, объясняющее детали."])
    segs = prepare_chapter(ch, Lang.ru)
    # "Да." and "Нет." alone are well under the 25-char minimum fragment size
    for s in segs[:-1]:
        assert len(s.text) >= 25 or s is segs[-1]


def test_segment_text_never_empty_or_punctuation_only():
    ch = _make_chapter(paragraphs=["Текст.", "...", "***", "[12]", "   ", "Ещё текст."])
    segs = prepare_chapter(ch, Lang.ru)
    for s in segs:
        assert s.text.strip()
        assert re.search(r"\w", s.text)


def test_concatenated_sources_reconstruct_original_modulo_whitespace():
    paragraph = "Первое предложение. Второе предложение. Третье предложение."
    ch = _make_chapter(paragraphs=[paragraph])
    segs = prepare_chapter(ch, Lang.ru)
    joined = " ".join(s.source for s in segs)
    assert re.sub(r"\s+", " ", joined).strip() == re.sub(r"\s+", " ", paragraph).strip()


# ----------------------------------------------------------------------------- mixed language

def test_mixed_language_paragraph_gets_its_own_lang():
    ch = _make_chapter(paragraphs=[
        "Это русский абзац с нормальным текстом.",
        "This entire paragraph is written in English inside a Russian book.",
    ])
    segs = prepare_chapter(ch, Lang.ru)
    assert segs[0].lang == Lang.ru
    assert segs[1].lang == Lang.en


def test_numeric_only_paragraph_falls_back_to_book_lang():
    ch = _make_chapter(paragraphs=["1234567890"])
    segs = prepare_chapter(ch, Lang.en)
    assert segs[0].lang == Lang.en
