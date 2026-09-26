"""Shared number <-> words helpers used by normalize_ru / normalize_en / compare_form.

Wraps num2words and adds the bits it doesn't do out of the box:
  - Roman numeral parsing (strict: only canonical forms are accepted).
  - Russian noun-number agreement (one/few/many) for common measure words.
  - English "spoken year" and "spoken decade" conventions (num2words' own
    to='year' doesn't match the 2000s convention we want, see en_year).

Only nominative-ish, nominative-plural agreement is implemented for
ru_plural_form: it's the correct output for a preceding cardinal read out of
context (the overwhelmingly common case in narrative prose: "три дома",
"пятнадцать процентов"). Full oblique-case agreement of the measure noun
itself (e.g. instrumental "тремя рублями") is not implemented; see gaps in
the module docstring of normalize_ru.
"""
from __future__ import annotations

from num2words import num2words
from num2words.lang_RU import Num2Word_RU

_ru_engine = Num2Word_RU()

# ----------------------------------------------------------------------------- Roman numerals

_ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_ROMAN_ORDER = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
                (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def int_to_roman(n: int) -> str:
    out = []
    for value, sym in _ROMAN_ORDER:
        while n >= value:
            out.append(sym)
            n -= value
    return "".join(out)


def roman_to_int(s: str) -> int | None:
    """Strict: rejects malformed numerals (IIII, VV, IC, ...) by round-tripping."""
    s = s.upper()
    if not s or any(ch not in _ROMAN_MAP for ch in s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        val = _ROMAN_MAP[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    if not (0 < total <= 3999) or int_to_roman(total) != s:
        return None
    return total


# ----------------------------------------------------------------------------- Russian

def ru_cardinal(n: int, case: str = "n", gender: str = "m", animate: bool = True) -> str:
    return _ru_engine.to_cardinal(n, case=case, gender=gender, animate=animate)


def ru_ordinal(n: int, case: str = "n", gender: str = "m") -> str:
    return _ru_engine.to_ordinal(n, case=case, gender=gender)


def ru_plural_form(n: int, forms: tuple[str, str, str]) -> str:
    """forms = (one, few, many), e.g. ('рубль', 'рубля', 'рублей')."""
    n = abs(n)
    last_two = n % 100
    last = n % 10
    if 11 <= last_two <= 14:
        return forms[2]
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def ru_unit(n: int, forms: tuple[str, str, str], gender: str = "m", case: str = "n") -> str:
    """n spelled out + the agreeing noun, e.g. ru_unit(3, RUB) -> 'три рубля'."""
    return f"{ru_cardinal(n, case=case, gender=gender)} {ru_plural_form(n, forms)}"


# Common measure-word tables (one, few, many).
RUB = ("рубль", "рубля", "рублей")
KOP = ("копейка", "копейки", "копеек")
USD = ("доллар", "доллара", "долларов")
EUR = ("евро", "евро", "евро")
GBP = ("фунт", "фунта", "фунтов")
PERCENT = ("процент", "процента", "процентов")
THOUSAND = ("тысяча", "тысячи", "тысяч")
MILLION = ("миллион", "миллиона", "миллионов")
BILLION = ("миллиард", "миллиарда", "миллиардов")
YEAR_PLURAL = ("год", "года", "лет")  # "N лет" (age/duration); "гг." range uses "годы"/"годов" instead


# ----------------------------------------------------------------------------- English

_EN_DECADE_WORDS = {
    0: "hundreds", 10: "tens", 20: "twenties", 30: "thirties", 40: "forties",
    50: "fifties", 60: "sixties", 70: "seventies", 80: "eighties", 90: "nineties",
}


def en_year(n: int) -> str:
    """Spoken form of a 4-digit year: 1891 -> 'eighteen ninety-one',
    1905 -> 'nineteen oh five', 2005 -> 'two thousand five', 2020 -> 'twenty twenty'."""
    if 2000 <= n <= 2009:
        rem = n - 2000
        return "two thousand" if rem == 0 else f"two thousand {num2words(rem, lang='en')}"
    if 1100 <= n <= 9999 and n % 100 != 0:
        hi, lo = divmod(n, 100)
        if lo < 10:
            return f"{num2words(hi, lang='en')} oh {num2words(lo, lang='en')}"
        return f"{num2words(hi, lang='en')} {num2words(lo, lang='en')}"
    return num2words(n, lang="en")


def en_decade(n: int) -> str:
    """1990 -> 'nineteen nineties' (for '1990s')."""
    century, decade = divmod(n, 100)
    word = _EN_DECADE_WORDS.get(decade, f"{num2words(decade, lang='en', to='ordinal')}s")
    return f"{num2words(century, lang='en')} {word}" if century else word
