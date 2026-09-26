import pytest

from mytts.contracts import Lang
from mytts.text import detect_lang


@pytest.mark.parametrize("text,expected", [
    ("Привет, как дела?", Lang.ru),
    ("Hello, how are you?", Lang.en),
    ("Он вернулся домой поздно вечером, but stayed.", Lang.ru),  # more cyrillic letters
    ("He came home late, а он остался.", Lang.en),  # more latin letters
    ("12345", Lang.ru),        # no letters at all -> default ru
    ("", Lang.ru),             # empty -> default ru
    ("...", Lang.ru),          # punctuation only -> default ru
    ("Ёжик", Lang.ru),
    ("XYZ", Lang.en),
])
def test_detect_lang(text, expected):
    assert detect_lang(text) == expected


def test_detect_lang_tie_defaults_ru():
    # exactly equal cyrillic/latin letter counts
    assert detect_lang("аб cd") == Lang.ru
