import pytest

from mytts.text.yo import restore_yo

POSITIVE_CASES = [
    ("еще", "ещё"),
    ("Еще не время.", "Ещё не время."),
    ("темный лес", "тёмный лес"),
    ("легкий ветер", "лёгкий ветер"),
    ("зеленая трава", "зелёная трава"),
    ("черный кот", "чёрный кот"),
    ("желтый лист", "жёлтый лист"),
    ("теплый день", "тёплый день"),
    ("твердый камень", "твёрдый камень"),
    ("тяжелый груз", "тяжёлый груз"),
    ("он идет домой", "он идёт домой"),
    ("они живут здесь", "они живут здесь"),  # "живут" has no ё form, unaffected
    ("она живет здесь", "она живёт здесь"),
    ("он пришел", "он пришёл"),
    ("он нашел ключ", "он нашёл ключ"),
    ("он ушел рано", "он ушёл рано"),
    ("ребенок спит", "ребёнок спит"),
    ("у березы", "у берёзы"),
    ("звезды на небе", "звёзды на небе"),
    ("мед и лед", "мёд и лёд"),
    ("мертвый город", "мёртвый город"),
    ("полет самолета", "полёт самолета"),  # "самолета" not in dict, left alone
    ("Костер горел долго", "Костёр горел долго"),
]

# Ambiguous minimal pairs: must NOT be touched.
NEGATIVE_CASES = [
    "Все ушли домой.",
    "Всё было хорошо.",
    "Небо было ясным.",
    "Он передохнет немного.",
]


@pytest.mark.parametrize("text,expected", POSITIVE_CASES)
def test_restore_yo_positive(text, expected):
    assert restore_yo(text) == expected


@pytest.mark.parametrize("text", NEGATIVE_CASES)
def test_restore_yo_ambiguous_untouched(text):
    assert restore_yo(text) == text


def test_restore_yo_idempotent():
    for text, expected in POSITIVE_CASES:
        assert restore_yo(expected) == expected


def test_restore_yo_preserves_capitalization():
    assert restore_yo("ЕЩЕ") == "ЕЩЁ"
    assert restore_yo("Темный") == "Тёмный"
