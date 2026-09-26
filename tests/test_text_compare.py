"""compare_form: canonical QA-scoring form, and the symmetry the QA pass relies on."""
import difflib

from mytts.contracts import Lang
from mytts.text import compare_form


def _cer_like(a: str, b: str) -> float:
    """Simple char-level distance ratio, standing in for the real CER metric."""
    if not a and not b:
        return 0.0
    return 1 - difflib.SequenceMatcher(None, a, b).ratio()


def test_compare_form_basic_shape():
    assert compare_form("Привет, МИР!!!", Lang.ru) == "привет мир"
    assert compare_form("Hello, WORLD!!!", Lang.en) == "hello world"


def test_compare_form_yo_folds_to_ye():
    assert compare_form("ещё", Lang.ru) == compare_form("еще", Lang.ru)


def test_compare_form_whitespace_collapsed():
    assert compare_form("много    пробелов\t\tтут", Lang.ru) == "много пробелов тут"


def test_compare_form_ru_year_digit_vs_words_close():
    # The Phase 0 false-alarm case: Whisper writes "1891" where the spoken
    # text said "тысяча восемьсот девяносто первом".
    a = compare_form("В 1891 году", Lang.ru)
    b = compare_form("в тысяча восемьсот девяносто первом году", Lang.ru)
    assert a == b
    assert _cer_like(a, b) < 0.12


def test_compare_form_en_year_digit_vs_words_close():
    a = compare_form("In 1891", Lang.en)
    b = compare_form("in eighteen ninety-one", Lang.en)
    assert a == b
    assert _cer_like(a, b) < 0.12


def test_compare_form_ru_money_digit_vs_words_close():
    a = compare_form("за 2500 руб.", Lang.ru)
    b = compare_form("за две тысячи пятьсот рублей", Lang.ru)
    assert _cer_like(a, b) < 0.12


def test_compare_form_symmetric_on_equal_inputs():
    text = "Это был тысяча восемьсот девяносто первый год."
    assert compare_form(text, Lang.ru) == compare_form(compare_form(text, Lang.ru), Lang.ru)


def test_compare_form_idempotent():
    for text, lang in [("В 1891 году", Lang.ru), ("In 1891", Lang.en)]:
        once = compare_form(text, lang)
        twice = compare_form(once, lang)
        assert once == twice
