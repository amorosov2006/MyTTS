"""English text normalization for TTS: numbers, dates, abbreviations, symbols, cleanup.

Pipeline (see `normalize`), most-specific patterns first for the same reason
as normalize_ru: a later generic pass must not re-touch text a more specific
one already produced.

  1. cleanup    - straighten quotes, spaced-hyphen-as-dash -> em dash.
  2. dates      - "Jan. 5, 1891" / "5 January 1891"
  3. decades    - "1990s" -> "nineteen nineties"
  4. years      - context-gated 4-digit year reading (see _looks_like_year)
  5. roman numerals - "Chapter IV" -> cardinal, "Henry VIII" -> ordinal "the Eighth"
  6. currency   - $/£/€, with "and N cents" for a decimal part
  7. percent    - "15%" / "3.5%"
  8. decimals   - "3.5" -> "three point five" (digit-by-digit fraction)
  9. time       - "10:30" -> "ten thirty"; "5 p.m." -> "five p m"
  10. ordinal digits - "1st", "21st"
  11. abbreviations  - Mr./Mrs./Dr./St./Jan.../Mon.../e.g./i.e./etc./...
  12. bare cardinals - whatever digit runs are left
  13. whitespace collapse

Known gaps:
  - Year detection is heuristic (a context word, a following comma, or a
    plausible bare 4-digit year sentence); an unusual sentence shape can
    still misread a 4-digit quantity as a year or vice versa.
  - Roman-numeral detection requires the numeral token to be ALL UPPERCASE
    (case-sensitive) to avoid matching ordinary words; an all-caps word made
    only of M/D/C/L/X/V/I letters (e.g. "MIX" shouted in dialogue) could
    still misfire. Single-letter numerals ("I", "V"...) are only converted
    right after a known numbering word (Chapter/Part/...), never in the
    "personal name" fallback, specifically to leave the pronoun "I" alone.
  - Dr./St. before-a-name vs. after-a-name disambiguation is a shallow
    capitalization heuristic, not real parsing.
"""
from __future__ import annotations

import re

from num2words import num2words as _num2words

from mytts.text import numbers as num


def num2words(n: int, **kwargs) -> str:
    """num2words(lang='en') inserts commas for readability ("two thousand,
    five hundred"); TTS text shouldn't have them (the spec's own examples
    spell it "two thousand five hundred dollars"), so strip them here once
    for every call site in this module instead of at each one."""
    return _num2words(n, lang="en", **kwargs).replace(",", "")


# ----------------------------------------------------------------------------- 1. cleanup

_QUOTES_RE = re.compile("[“”„‘’]")
_SPACED_HYPHEN_RE = re.compile(r"(?<=\S) - (?=\S)")
_DIGIT_DASH_DIGIT_RE = re.compile(r"(?<=\d)-(?=\d)")


def _straighten_quote(m: re.Match) -> str:
    return '"' if m.group(0) in "“”„" else "'"


def _cleanup(text: str) -> str:
    text = _QUOTES_RE.sub(_straighten_quote, text)
    text = _SPACED_HYPHEN_RE.sub(" — ", text)
    text = _DIGIT_DASH_DIGIT_RE.sub(" — ", text)
    return text


# ----------------------------------------------------------------------------- 2. dates

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_NAMES = ["", "January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

# "Jan. 5, 1891" / "January 5, 1891"
_DATE_MDY_RE = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>\d{{3,4}})\b",
    re.IGNORECASE,
)
# "5 January 1891"
_DATE_DMY_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_PATTERN})\.?,?\s+(?P<year>\d{{3,4}})\b",
    re.IGNORECASE,
)


def _ordinal_day(day: int) -> str:
    return num2words(day, to="ordinal")


def _replace_dates(text: str) -> str:
    def sub(m: re.Match) -> str:
        month = _MONTH_NAMES[_MONTHS[m.group("month").lower()]]
        day_words = _ordinal_day(int(m.group("day")))
        year_words = num.en_year(int(m.group("year")))
        return f"{month} {day_words}, {year_words}"

    text = _DATE_MDY_RE.sub(sub, text)
    text = _DATE_DMY_RE.sub(sub, text)
    return text


# ----------------------------------------------------------------------------- 3. decades

_DECADE_RE = re.compile(r"\b(\d{2,4}0)s\b")


def _replace_decades(text: str) -> str:
    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        if not (1000 <= n <= 2090 and n % 10 == 0):
            return m.group(0)
        return num.en_decade(n)

    return _DECADE_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 4. years

_YEAR_CONTEXT_WORDS = {"in", "of", "since", "by", "until", "before", "after", "around", "circa", "from", "to"}
_YEAR_RE = re.compile(r"\b(?P<ctx>[A-Za-z]+\s+)?(?P<y>1[1-9]\d{2}|20\d{2})\b(?P<comma>,)?")


def _replace_years(text: str) -> str:
    def sub(m: re.Match) -> str:
        ctx = (m.group("ctx") or "").strip().lower()
        year_like = ctx in _YEAR_CONTEXT_WORDS or bool(m.group("comma"))
        if not year_like:
            return m.group(0)
        prefix = m.group("ctx") or ""
        suffix = m.group("comma") or ""
        return f"{prefix}{num.en_year(int(m.group('y')))}{suffix}"

    return _YEAR_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 5. roman numerals

_ROMAN_TOKEN = r"[IVXLCDM]+"  # case-sensitive: all-uppercase only, see module docstring
_CARDINAL_CONTEXT = {
    "chapter", "part", "book", "volume", "act", "war", "number", "no",
    "section", "verse", "scene", "episode", "step", "world",
}
_ROMAN_RE = re.compile(rf"\b([A-Za-z]+)\s+({_ROMAN_TOKEN})\b")


def _replace_romans(text: str) -> str:
    def sub(m: re.Match) -> str:
        ctx_word, roman = m.group(1), m.group(2)
        n = num.roman_to_int(roman)
        if n is None:
            return m.group(0)
        if ctx_word.lower() in _CARDINAL_CONTEXT:
            return f"{ctx_word} {num2words(n)}"
        if len(roman) >= 2:
            ordinal = num2words(n, to="ordinal")
            return f"{ctx_word} the {ordinal.capitalize()}"
        return m.group(0)  # single-letter numeral, no numbering context: leave it (e.g. pronoun "I")

    return _ROMAN_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 6. currency

_CURRENCY_INT = r"\d{1,3}(?:,\d{3})*"
_CURRENCY_RE = re.compile(rf"([$£€])\s*({_CURRENCY_INT})(?:\.(\d{{2}}))?")
_CURRENCY_UNITS = {
    "$": (("dollar", "dollars"), ("cent", "cents")),
    "£": (("pound", "pounds"), ("pence", "pence")),
    "€": (("euro", "euros"), ("cent", "cents")),
}


def _replace_currency(text: str) -> str:
    def sub(m: re.Match) -> str:
        sign, int_str, cents_str = m.group(1), m.group(2), m.group(3)
        (unit_one, unit_many), (sub_one, sub_many) = _CURRENCY_UNITS[sign]
        n = int(int_str.replace(",", ""))
        main = f"{num2words(n)} {unit_one if n == 1 else unit_many}"
        if cents_str and int(cents_str) != 0:
            c = int(cents_str)
            main += f" and {num2words(c)} {sub_one if c == 1 else sub_many}"
        return main

    return _CURRENCY_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 7. percent

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _replace_percent(text: str) -> str:
    def sub(m: re.Match) -> str:
        return f"{_number_words(m.group(1))} percent"

    return _PERCENT_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 8. decimals

_DECIMAL_RE = re.compile(r"\b\d+\.\d+\b")


def _number_words(raw: str) -> str:
    if "." not in raw:
        return num2words(int(raw))
    int_part, frac_part = raw.split(".", 1)
    int_words = num2words(int(int_part))
    frac_words = " ".join(num2words(int(d)) for d in frac_part)
    return f"{int_words} point {frac_words}"


def _replace_decimals(text: str) -> str:
    return _DECIMAL_RE.sub(lambda m: _number_words(m.group(0)), text)


# ----------------------------------------------------------------------------- 9. time

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_MERIDIEM_RE = re.compile(r"\b(\d{1,2})\s*([ap])\.?m\.?\b", re.IGNORECASE)


def _replace_time(text: str) -> str:
    def sub(m: re.Match) -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        hour_words = num2words(hour)
        if minute == 0:
            return f"{hour_words} o'clock"
        if minute < 10:
            return f"{hour_words} oh {num2words(minute)}"
        return f"{hour_words} {num2words(minute)}"

    text = _TIME_RE.sub(sub, text)
    text = _MERIDIEM_RE.sub(lambda m: f"{num2words(int(m.group(1)))} {m.group(2).lower()} m", text)
    return text


# ----------------------------------------------------------------------------- 10. ordinal digits

_ORDINAL_DIGIT_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b", re.IGNORECASE)


def _replace_ordinal_digits(text: str) -> str:
    return _ORDINAL_DIGIT_RE.sub(lambda m: num2words(int(m.group(1)), to="ordinal"), text)


# ----------------------------------------------------------------------------- 11. abbreviations

_DAYS = {"mon": "Monday", "tue": "Tuesday", "tues": "Tuesday", "wed": "Wednesday",
          "thu": "Thursday", "thur": "Thursday", "thurs": "Thursday", "fri": "Friday",
          "sat": "Saturday", "sun": "Sunday"}

_SIMPLE_ABBREVS: list[tuple[str, str]] = [
    (r"\be\.g\.", "for example"),
    (r"\bi\.e\.", "that is"),
    (r"\betc\.", "et cetera"),
    (r"\bvs\.", "versus"),
    (r"\bapprox\.", "approximately"),
    (r"\bProf\.", "Professor"),
    (r"\bGen\.", "General"),
    (r"\bCapt\.", "Captain"),
    (r"\bLt\.", "Lieutenant"),
    (r"\bJr\.", "Junior"),
    (r"\bSr\.", "Senior"),
    (r"\bMrs\.", "Missus"),
    (r"\bMs\.", "Miss"),
    (r"\bMr\.", "Mister"),
]
_SIMPLE_ABBREV_RES = [(re.compile(p), r) for p, r in _SIMPLE_ABBREVS]

_DR_BEFORE_NAME_RE = re.compile(r"\bDr\.\s*(?=[A-Z][a-z])")
_DR_LEFTOVER_RE = re.compile(r"\bDr\.")
_ST_BEFORE_NAME_RE = re.compile(r"\bSt\.\s*(?=[A-Z][a-z])")
_ST_LEFTOVER_RE = re.compile(r"\bSt\.")
_NO_BEFORE_NUMBER_RE = re.compile(r"\bNo\.\s*(?=\d)", re.IGNORECASE)


def _match_case(original: str, replacement: str) -> str:
    if original and original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


_MONTH_ABBREV_RE = re.compile(
    r"\b(" + "|".join(m for m in _MONTHS if len(m) <= 4) + r")\.", re.IGNORECASE
)
_DAY_ABBREV_RE = re.compile(r"\b(" + "|".join(_DAYS) + r")\.", re.IGNORECASE)


def _replace_abbreviations(text: str) -> str:
    text = _DR_BEFORE_NAME_RE.sub("Doctor ", text)
    text = _DR_LEFTOVER_RE.sub("Drive", text)
    text = _ST_BEFORE_NAME_RE.sub("Saint ", text)
    text = _ST_LEFTOVER_RE.sub("Street", text)
    text = _NO_BEFORE_NUMBER_RE.sub(lambda m: _match_case(m.group(0), "number "), text)
    for pattern, replacement in _SIMPLE_ABBREV_RES:
        text = pattern.sub(replacement, text)
    text = _MONTH_ABBREV_RE.sub(lambda m: _MONTH_NAMES[_MONTHS[m.group(1).lower()]], text)
    text = _DAY_ABBREV_RE.sub(lambda m: _DAYS[m.group(1).lower()], text)
    return text


# ----------------------------------------------------------------------------- 12. bare cardinals

_BARE_NUMBER_RE = re.compile(r"\d+")


def _replace_bare_cardinals(text: str) -> str:
    return _BARE_NUMBER_RE.sub(lambda m: num2words(int(m.group(0))), text)


# ----------------------------------------------------------------------------- entry point

_WS_RE = re.compile(r"[ \t]+")
_TERMINAL_PUNCT = ".!?…"


def _restore_terminal_punct(original: str, result: str) -> str:
    orig_stripped = original.rstrip()
    if orig_stripped and orig_stripped[-1] in _TERMINAL_PUNCT:
        if not result or result[-1] not in _TERMINAL_PUNCT:
            result += orig_stripped[-1]
    return result


def normalize(text: str) -> str:
    original = text
    text = _cleanup(text)
    text = _replace_dates(text)
    text = _replace_decades(text)
    text = _replace_years(text)
    text = _replace_romans(text)
    text = _replace_currency(text)
    text = _replace_percent(text)
    text = _replace_decimals(text)
    text = _replace_time(text)
    text = _replace_ordinal_digits(text)
    # Abbreviations before the generic cardinal pass: "No. 5" needs the digit
    # still a digit, and this keeps every remaining bare number pass simple.
    text = _replace_abbreviations(text)
    text = _replace_bare_cardinals(text)
    text = _WS_RE.sub(" ", text).strip()
    text = _restore_terminal_punct(original, text)
    return text
