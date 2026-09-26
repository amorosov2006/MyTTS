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
    # names and patronymics (unambiguous)
    ("Петр вышел из дома.", "Пётр вышел из дома."),
    ("Мы говорили с Семеном.", "Мы говорили с Семёном."),
    ("Федор Достоевский", "Фёдор Достоевский"),
    ("Артем и Артемович", "Артём и Артёмович"),
    ("Алена, Лева и Леня", "Алёна, Лёва и Лёня"),
    ("Потемкин, Королев, Хрущев, Горбачев", "Потёмкин, Королёв, Хрущёв, Горбачёв"),
]

# Ambiguous minimal pairs: must NOT be touched.
NEGATIVE_CASES = [
    "Все ушли домой.",
    "Всё было хорошо.",
    "Небо было ясным.",
    "Он передохнет немного.",
    "Петра позвали в дом.",       # Пётр's oblique cases have no ё
    "Петру передали письмо.",
    "Королева вышла на балкон.",  # the common noun "queen", not the surname
    "Много лени в этом деле.",    # the common noun "laziness", not Леня
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
