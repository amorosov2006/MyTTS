"""Russian text normalization for TTS: numbers, dates, abbreviations, symbols, cleanup, ё.

Pipeline (see `normalize`), most-specific patterns first so a later, more
generic pass (e.g. the bare-cardinal-number regex) never re-processes text a
more specific one (a date, a year, a money amount...) already handled:

  1. cleanup       - strip stress marks / soft hyphens, straighten quotes,
                      turn a spaced hyphen used as a dash into an em dash.
  2. restore_yo     - е -> ё for a curated, unambiguous word list (yo.py).
  3. dates          - "5 января 1891 г."
  4. year ranges    - "1941-1945 гг." / "в 1941-1945 годах"
  5. bare years     - "в 1891 году" / "1891 г."
  6. centuries      - "XIX век" / "в XIX веке" / "Глава IV" / "Часть III"
  7. money          - руб./коп./$/€/£, with noun agreement
  8. тыс./млн/млрд  - "5 тыс. человек" -> "пять тысяч человек"
  9. percent        - "15%"
  10. decimals      - "3,5" -> "три целых пять десятых"
  11. time          - "10:30" -> "десять тридцать"
  12. ordinal digits - "1-й", "2-го", "5-я"
  13. thousands seps - "1 000 000" -> digits merged, then read as one cardinal
  14. bare cardinals - whatever digit runs are left
  15. abbreviations  - т.е./т.к./ул./кв./им./проф./..., "г." fallback/city
  16. symbols        - №, §, &, °, arithmetic "+"
  17. whitespace collapse

Known gaps (documented, not fixed here — see the module's final report too):
  - Bare cardinals ending in 1 or 2 (not 11/12) get a gender guess from the
    ENDING of the word right after them (_gender_from_next_word: -а/-я/-ы/-и
    -> feminine, -о/-е -> neuter, else masculine — no lexicon, see that
    function's docstring). This covers the common cases ("2 книги" -> "две
    книги") but isn't real morphology: a borrowed or irregular noun whose
    ending doesn't match its actual gender can still get the wrong form.
    Cardinals ending in 3-9 have only one form regardless of gender, so this
    doesn't apply to them.
  - Abbreviation expansions (ул., кв., "д." -> дом, г., пр., пл.) re-decline
    to match a directly-preceding preposition. по/с/со/до/у use a fixed case
    (_PREP_ABBREV_CASE) since they're never directional. в/на ARE directional,
    so their case (accusative vs. prepositional) is guessed from the ending
    of the word right after the abbreviation (_V_NA_ABBREV_NOUNS /
    _replace_v_na_abbrevs) -- "в г. Москву" (accusative, -у) vs. "в г. Москве"
    (prepositional). Without a recognized preposition right in front (e.g.
    "ул. Садовой," after a comma) or a following word to read an ending from
    (e.g. "в д. 5"), they still substitute a fixed base form.
  - "г."/"гг."/"в."/"вв." left over after the number-aware and preposition-
    aware passes fall back to a best guess (год/годы/век/века) rather than
    true disambiguation.
  - Decimal reading only handles 1-2 fractional digits (десятых/сотых); more
    digits fall back to "тысячных" digit-by-digit still as one cardinal.
"""
from __future__ import annotations

import re

from mytts.text import numbers as num
from mytts.text.yo import restore_yo

# ----------------------------------------------------------------------------- 1. cleanup

_STRESS_MARK = "́"
_SOFT_HYPHEN = "­"
_QUOTES_RE = re.compile("[«»„“”“”„]")
_SPACED_HYPHEN_RE = re.compile(r"(?<=\S) - (?=\S)")
_DIGIT_DASH_DIGIT_RE = re.compile(r"(?<=\d)-(?=\d)")


def _cleanup(text: str) -> str:
    text = text.replace(_STRESS_MARK, "").replace(_SOFT_HYPHEN, "")
    text = _QUOTES_RE.sub('"', text)
    text = _SPACED_HYPHEN_RE.sub(" — ", text)
    # "10-15" -> "10 — 15": lets the year-range regex (which allows spaces
    # around the dash) match too, and reads better once spelled out.
    text = _DIGIT_DASH_DIGIT_RE.sub(" — ", text)
    return text


# ----------------------------------------------------------------------------- helpers

_MONTHS_GEN = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# case -> word form of "год" (used with a preceding preposition or standalone)
_GOD_SG = {"n": "год", "g": "года", "d": "году", "a": "год", "i": "годом", "p": "году"}
_GOD_PL = {"n": "годы", "g": "годов", "d": "годам", "a": "годы", "i": "годами", "p": "годах"}

_PREP_CASE = {
    "в": "p", "во": "p",
    "до": "g", "с": "g", "со": "g", "после": "g", "от": "g", "из": "g", "без": "g",
    "к": "d", "по": "d",
    "через": "a", "про": "a",
    "над": "i", "под": "i", "перед": "i", "между": "i",
}


def _match_case(original: str, replacement: str) -> str:
    if original and original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement


# ----------------------------------------------------------------------------- 3. dates

_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS_GEN) + r")\s+"
    # the trailing year marker is consumed as ONE unit (or not at all) --
    # "г?\.?" would independently match a bare "г" out of "года", leaving a
    # stray "ода" behind and duplicating the "года" this function appends.
    r"(?P<year>\d{3,4})(?:\s*(?:г\.|году|годом|года|год))?(?!\w)",
    re.IGNORECASE,
)


def _replace_dates(text: str) -> str:
    def sub(m: re.Match) -> str:
        day = int(m.group("day"))
        year = int(m.group("year"))
        day_words = num.ru_ordinal(day, case="g", gender="m")
        year_words = num.ru_ordinal(year, case="g", gender="m")
        return f"{day_words} {m.group('month')} {year_words} года"

    return _DATE_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 4/5. years

# NB: a suffix ending in a literal period (г., гг.) must not be followed by a
# trailing \b -- \b never matches between two non-word characters (the period
# and the space/EOS after it), so a plain "...\b" would silently never match.
# (?!\w) is used instead where needed.

_YEAR_RANGE_RE = re.compile(
    r"\b(?:(?P<prep>в|во|с|со|до|после)\s+)?(?P<y1>\d{3,4})\s*[-–—]\s*(?P<y2>\d{3,4})\s*"
    r"(?P<suf>гг\.|годах|годов|годы)(?!\w)",
    re.IGNORECASE,
)

_YEAR_RE = re.compile(
    r"\b(?:(?P<prep>в|во|с|со|до|после|от|из)\s+)?(?P<y>\d{3,4})\s*"
    r"(?P<suf>г\.|году|годом|года|год)(?!\w)",
    re.IGNORECASE,
)


def _replace_year_ranges(text: str) -> str:
    def sub(m: re.Match) -> str:
        prep = (m.group("prep") or "").lower()
        case = _PREP_CASE.get(prep, "n")
        y1, y2 = int(m.group("y1")), int(m.group("y2"))
        w1 = num.ru_ordinal(y1, case=case, gender="m")
        w2 = num.ru_ordinal(y2, case=case, gender="m")
        god = _GOD_PL.get(case, "годы")
        prefix = f"{m.group('prep')} " if m.group("prep") else ""
        out = f"{prefix}{w1} — {w2} {god}"
        return _match_case(m.group(0), out)

    return _YEAR_RANGE_RE.sub(sub, text)


def _replace_years(text: str) -> str:
    def sub(m: re.Match) -> str:
        prep = (m.group("prep") or "").lower()
        case = _PREP_CASE.get(prep, "n")
        y = int(m.group("y"))
        year_words = num.ru_ordinal(y, case=case, gender="m")
        god = _GOD_SG.get(case, "год")
        prefix = f"{m.group('prep')} " if m.group("prep") else ""
        out = f"{prefix}{year_words} {god}"
        return _match_case(m.group(0), out)

    return _YEAR_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 6. centuries

_CENTURY_NOUNS = {
    "век": ("n", "m"), "века": ("g", "m"), "веку": ("d", "m"),
    "веком": ("i", "m"), "веке": ("p", "m"),
    "столетие": ("n", "n"), "столетия": ("g", "n"), "столетию": ("d", "n"),
    "столетием": ("i", "n"), "столетии": ("p", "n"),
}
_ROMAN_TOKEN = r"(?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
_CENTURY_RE = re.compile(
    r"\b(?:(?P<prep>в|во|до|с|со|после)\s+)?(?P<roman>" + _ROMAN_TOKEN + r")\s+"
    r"(?P<noun>" + "|".join(_CENTURY_NOUNS) + r")\b",
    re.IGNORECASE,
)

_LABEL_GENDER = {"глава": "f", "часть": "f", "том": "m", "съезд": "m"}
_LABEL_RE = re.compile(
    r"\b(?P<label>" + "|".join(_LABEL_GENDER) + r")\s+(?P<roman>" + _ROMAN_TOKEN + r")\b",
    re.IGNORECASE,
)


def _at_sentence_start(full_text: str, pos: int) -> bool:
    prefix = full_text[:pos].rstrip()
    return not prefix or prefix[-1] in ".!?…\"'«"


def _replace_centuries(text: str) -> str:
    def sub(m: re.Match) -> str:
        n = num.roman_to_int(m.group("roman"))
        if n is None:
            return m.group(0)
        case, gender = _CENTURY_NOUNS[m.group("noun").lower()]
        word = num.ru_ordinal(n, case=case, gender=gender)
        # The roman numeral itself is always uppercase, so its own case can't
        # signal "start of sentence" -- check the real preceding text instead.
        if not m.group("prep") and _at_sentence_start(m.string, m.start()):
            word = word[0].upper() + word[1:]
        prefix = f"{m.group('prep')} " if m.group("prep") else ""
        return f"{prefix}{word} {m.group('noun')}"

    return _CENTURY_RE.sub(sub, text)


def _replace_labeled_romans(text: str) -> str:
    def sub(m: re.Match) -> str:
        n = num.roman_to_int(m.group("roman"))
        if n is None:
            return m.group(0)
        gender = _LABEL_GENDER[m.group("label").lower()]
        word = num.ru_ordinal(n, case="n", gender=gender)
        return f"{m.group('label')} {word}"

    return _LABEL_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 7. money

# Grouped integers only (no decimal cents in RU money -- see module docstring gaps).
_RU_INT = r"\d+(?:[  ]\d{3})*"
_SIGN_INT = r"\d+(?:[  ,]\d{3})*"  # sign-prefixed amounts may use "," as thousands sep too

_RUB_RE = re.compile(rf"\b({_RU_INT})\s*(?:руб\.|рублей|рубля|рубль|р\.)(?!\w)", re.IGNORECASE)
_KOP_RE = re.compile(rf"\b({_RU_INT})\s*(?:коп\.|копеек|копейки|копейка)(?!\w)", re.IGNORECASE)
_USD_SIGN_RE = re.compile(rf"\$\s*({_SIGN_INT})")
_EUR_SIGN_RE = re.compile(rf"€\s*({_SIGN_INT})")
_GBP_SIGN_RE = re.compile(rf"£\s*({_SIGN_INT})")
_USD_WORD_RE = re.compile(rf"\b({_RU_INT})\s*(?:долларов|доллара|доллар)\b", re.IGNORECASE)


def _digits(s: str) -> int:
    return int(re.sub(r"[\s ,]", "", s))


def _replace_money(text: str) -> str:
    text = _RUB_RE.sub(lambda m: num.ru_unit(_digits(m.group(1)), num.RUB), text)
    text = _KOP_RE.sub(lambda m: num.ru_unit(_digits(m.group(1)), num.KOP, gender="f"), text)
    text = _USD_WORD_RE.sub(lambda m: num.ru_unit(_digits(m.group(1)), num.USD), text)
    text = _USD_SIGN_RE.sub(lambda m: num.ru_unit(_digits(m.group(1)), num.USD), text)
    text = _EUR_SIGN_RE.sub(lambda m: num.ru_unit(_digits(m.group(1)), num.EUR), text)
    text = _GBP_SIGN_RE.sub(lambda m: num.ru_unit(_digits(m.group(1)), num.GBP), text)
    return text


# ----------------------------------------------------------------------------- 8. тыс./млн/млрд

_SCALE_RE = re.compile(r"\b(\d[\d\s ]*)\s*(тыс\.|млн\.?|млрд\.?)", re.IGNORECASE)
_SCALE_FORMS = {"тыс": num.THOUSAND, "млн": num.MILLION, "млрд": num.BILLION}


def _replace_scale(text: str) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(2).lower().rstrip(".")
        return num.ru_unit(_digits(m.group(1)), _SCALE_FORMS[key])

    return _SCALE_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 9. percent

_PERCENT_RE = re.compile(r"(\d[\d\s .,]*)\s*%")


def _replace_percent(text: str) -> str:
    def sub(m: re.Match) -> str:
        raw = m.group(1).strip()
        if "," in raw or "." in raw:
            return f"{_decimal_words(raw)} процента"
        return num.ru_unit(_digits(raw), num.PERCENT)

    return _PERCENT_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 10. decimals

_DECIMAL_RE = re.compile(r"\b\d+,\d+\b")
_FRACTION_WORD = {1: ("десятая", "десятых"), 2: ("сотая", "сотых"), 3: ("тысячная", "тысячных")}


def _decimal_words(raw: str) -> str:
    int_part, frac_part = re.split(r"[.,]", raw, maxsplit=1)
    int_n = int(int_part)
    frac_n = int(frac_part)
    int_words = num.ru_unit(int_n, ("целая", "целых", "целых"), gender="f")
    singular, genpl = _FRACTION_WORD.get(len(frac_part), _FRACTION_WORD[3])
    frac_word = singular if frac_n % 10 == 1 and frac_n % 100 != 11 else genpl
    frac_words = num.ru_cardinal(frac_n, gender="f")
    return f"{int_words} {frac_words} {frac_word}"


def _replace_decimals(text: str) -> str:
    return _DECIMAL_RE.sub(lambda m: _decimal_words(m.group(0)), text)


# ----------------------------------------------------------------------------- 11. time

_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})\b")


def _replace_time(text: str) -> str:
    def sub(m: re.Match) -> str:
        hour, minute = int(m.group(1)), int(m.group(2))
        hour_words = num.ru_cardinal(hour)
        if minute == 0:
            return f"{hour_words} ноль-ноль"
        if minute < 10:
            return f"{hour_words} ноль {num.ru_cardinal(minute, gender='f')}"
        return f"{hour_words} {num.ru_cardinal(minute, gender='f')}"

    return _TIME_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 12. ordinal digits

_ORDINAL_SUFFIXES = {
    "го": ("g", "m", False), "му": ("d", "m", False), "ым": ("i", "m", False),
    "ой": ("g", "f", False), "ую": ("a", "f", False),
    "ых": ("g", "m", True), "ми": ("i", "m", True),
    "й": ("n", "m", False), "м": ("p", "m", False),
    "я": ("n", "f", False), "е": ("n", "n", False), "х": ("g", "m", True),
}
_ORDINAL_DIGIT_RE = re.compile(
    r"\b(\d+)-(" + "|".join(sorted(_ORDINAL_SUFFIXES, key=len, reverse=True)) + r")\b"
)


def _replace_ordinal_digits(text: str) -> str:
    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        case, gender, plural = _ORDINAL_SUFFIXES[m.group(2)]
        return num.ru_ordinal(n, case=case, gender=gender if not plural else "m")

    return _ORDINAL_DIGIT_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 13/14. cardinals

_THOUSANDS_SEP_RE = re.compile(r"(?<=\d)[  ](?=\d{3}\b)")
_BARE_NUMBER_RE = re.compile(r"\d+")
_NEXT_WORD_RE = re.compile(r"\s*([A-Za-zА-яЁё]+)")


def _collapse_thousands_seps(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _THOUSANDS_SEP_RE.sub("", text)
    return text


def _gender_from_next_word(text: str, pos: int, *, digit_is_one: bool) -> str:
    """Heuristic gender guess for a number ending in 1 or 2 (not 11/12), read
    off the ending of the noun the ORIGINAL author already wrote right after
    it -- no lexicon, just the ending. Russian marks gender on the number
    itself here (один/одна/одно, два/две), unlike 3+ which have one form.

    For "1 X": X is nominative singular, so its own gender ending shows
    directly (книга/окно/дом). For "2 X": X is genitive singular, which
    for masculine and neuter nouns end in -а/-я (дома, окна) and for
    feminine nouns end in -ы/-и (книги, минуты).
    """
    m = _NEXT_WORD_RE.match(text, pos)
    if not m:
        return "m"
    last = m.group(1)[-1].lower()
    if digit_is_one:
        if last in "ая":
            return "f"
        if last in "ое":
            return "n"
        return "m"  # consonant, -й, soft sign, or unknown: default masculine
    if last in "ыи":
        return "f"
    return "m"  # -а/-я (masc/neut genitive singular) or unknown: default masculine


def _replace_bare_cardinals(text: str) -> str:
    def sub(m: re.Match) -> str:
        n = int(m.group(0))
        last, last_two = n % 10, n % 100
        gender = "m"
        if last == 1 and last_two != 11:
            gender = _gender_from_next_word(text, m.end(), digit_is_one=True)
        elif last == 2 and last_two != 12:
            gender = _gender_from_next_word(text, m.end(), digit_is_one=False)
        return num.ru_cardinal(n, gender=gender)

    return _BARE_NUMBER_RE.sub(sub, text)


# ----------------------------------------------------------------------------- 15. abbreviations

_SIMPLE_ABBREVS: list[tuple[str, str]] = [
    (r"\bт\.\s?е\.", "то есть"),
    (r"\bт\.\s?к\.", "так как"),
    (r"\bт\.\s?д\.", "так далее"),
    (r"\bт\.\s?п\.", "тому подобное"),
    (r"\bи\s+др\.", "и другие"),
    (r"\bдр\.", "другие"),
    (r"\bсм\.", "смотри"),
    (r"\bстр\.", "страница"),
    (r"\bул\.", "улица"),
    (r"\bкв\.", "квартира"),
    (r"\bим\.", "имени"),
    (r"\bпроф\.", "профессор"),
    (r"\bакад\.", "академик"),
]
_SIMPLE_ABBREV_RES = [(re.compile(p, re.IGNORECASE), r) for p, r in _SIMPLE_ABBREVS]

_DOM_BEFORE_NUMBER_RE = re.compile(r"\bд\.\s*(?=\d)", re.IGNORECASE)
_GOROD_RE = re.compile(r"\bг\.\s*(?=[А-ЯЁ])", re.IGNORECASE)
_LEFTOVER_G_RE = re.compile(r"\bг\.", re.IGNORECASE)
_LEFTOVER_GG_RE = re.compile(r"\bгг\.", re.IGNORECASE)
_LEFTOVER_V_RE = re.compile(r"\bв\.", re.IGNORECASE)
_LEFTOVER_VV_RE = re.compile(r"\bвв\.", re.IGNORECASE)

# Preposition-aware case agreement for abbreviations, applied ONLY when the
# preposition directly precedes the abbreviation. This runs before the
# simple, case-agreement-free expansions below (which still handle "ул."/
# "д." with no recognized preposition in front, e.g. "ул. Садовой," after a
# comma) -- see the module docstring gap this narrows but doesn't close.
#
# по/с/со/до/у are not directional, so their case never depends on what
# follows: "по улице" (dative), "с улицы"/"до улицы" (genitive), "у дома"
# (genitive) are always the same form.
_PREP_ABBREV_CASE = {
    ("по", "ул"): "улице",
    ("с", "ул"): "улицы",
    ("со", "ул"): "улицы",
    ("до", "ул"): "улицы",
    ("у", "д"): "дома",
}
_PREP_ABBREV_RE = re.compile(
    r"\b(?P<prep>по|со|с|до|у)\s+(?P<abbrev>ул|д)\.",
    re.IGNORECASE,
)

# в/на ARE directional: "в город" (accusative, going there) vs. "в городе"
# (prepositional, being there). Real Russian text already marks this on the
# NEXT word ("в город Москву" vs. "в городе Москве"), so peek at its ending
# to pick the case: -у/-ю (which also covers -ую/-юю) -> accusative, anything
# else (including no word at all, e.g. a house/flat number) -> prepositional.
# квартира is always "в", regardless of whether the source wrote "на кв." or
# "в кв." -- real usage never says "на квартиру/квартире".
_V_NA_ABBREV_NOUNS = {
    "г": {"acc": "город", "prep": "городе"},
    "ул": {"acc": "улицу", "prep": "улице"},
    "д": {"acc": "дом", "prep": "доме"},
    "кв": {"acc": "квартиру", "prep": "квартире", "force_prep": "в"},
    "пр": {"acc": "проспект", "prep": "проспекте"},
    "пл": {"acc": "площадь", "prep": "площади"},
}
_V_NA_ABBREV_RE = re.compile(
    r"\b(?P<prep>в|во|на)\s+(?P<abbrev>г|ул|д|кв|пр|пл)\.\s*",
    re.IGNORECASE,
)
_ACCUSATIVE_ENDING = ("у", "ю")


def _replace_v_na_abbrevs(text: str) -> str:
    def sub(m: re.Match) -> str:
        info = _V_NA_ABBREV_NOUNS.get(m.group("abbrev").lower())
        if info is None:
            return m.group(0)
        word_m = _NEXT_WORD_RE.match(text, m.end())
        accusative = bool(word_m and word_m.group(1)[-1].lower() in _ACCUSATIVE_ENDING)
        noun = info["acc"] if accusative else info["prep"]
        prep = info.get("force_prep", m.group("prep"))
        return _match_case(m.group(0), f"{prep} {noun} ")

    return _V_NA_ABBREV_RE.sub(sub, text)


def _replace_prep_abbrevs(text: str) -> str:
    def sub(m: re.Match) -> str:
        noun = _PREP_ABBREV_CASE.get((m.group("prep").lower(), m.group("abbrev").lower()))
        if noun is None:
            return m.group(0)
        return f"{m.group('prep')} {noun}"

    text = _replace_v_na_abbrevs(text)
    text = _PREP_ABBREV_RE.sub(sub, text)
    return text


def _replace_abbreviations(text: str) -> str:
    text = _replace_prep_abbrevs(text)
    text = _DOM_BEFORE_NUMBER_RE.sub(lambda m: _match_case(m.group(0), "дом "), text)
    text = _GOROD_RE.sub(lambda m: _match_case(m.group(0), "город "), text)
    for pattern, replacement in _SIMPLE_ABBREV_RES:
        text = pattern.sub(lambda m, r=replacement: _match_case(m.group(0), r), text)
    text = _LEFTOVER_GG_RE.sub(lambda m: _match_case(m.group(0), "годы"), text)
    text = _LEFTOVER_G_RE.sub(lambda m: _match_case(m.group(0), "год"), text)
    text = _LEFTOVER_VV_RE.sub(lambda m: _match_case(m.group(0), "века"), text)
    text = _LEFTOVER_V_RE.sub(lambda m: _match_case(m.group(0), "век"), text)
    return text


# ----------------------------------------------------------------------------- 16. symbols

# № and § read naturally as a word BEFORE the number ("№ 5" -> "номер пять");
# ° reads naturally AFTER the number ("20°" -> "двадцать градусов"). Each gets
# the space it needs on its own side; a final whitespace collapse tidies up
# any resulting double space.
_NUMBER_PREFIX_SYMBOLS = {"№": "номер", "§": "параграф"}
_NUMBER_SUFFIX_SYMBOLS = {"°": "градусов"}
_PREFIX_SYMBOL_RE = re.compile("|".join(re.escape(s) for s in _NUMBER_PREFIX_SYMBOLS))
_SUFFIX_SYMBOL_RE = re.compile("|".join(re.escape(s) for s in _NUMBER_SUFFIX_SYMBOLS))
_AMP_RE = re.compile(r"&")
_PLUS_RE = re.compile(r"(?<=\d)\s*\+\s*(?=\d)")


def _replace_symbols(text: str) -> str:
    text = _PREFIX_SYMBOL_RE.sub(lambda m: _NUMBER_PREFIX_SYMBOLS[m.group(0)] + " ", text)
    text = _SUFFIX_SYMBOL_RE.sub(lambda m: " " + _NUMBER_SUFFIX_SYMBOLS[m.group(0)], text)
    text = _AMP_RE.sub(" и ", text)
    text = _PLUS_RE.sub(" плюс ", text)
    return text


# ----------------------------------------------------------------------------- entry point

_WS_RE = re.compile(r"[ \t]+")
_TERMINAL_PUNCT = ".!?…"


def _restore_terminal_punct(original: str, result: str) -> str:
    """An abbreviation period that also happened to end the sentence
    ("...за 2500 руб.") is consumed as part of expanding the abbreviation
    (руб. -> рублей). Put the sentence terminator back if it went missing."""
    orig_stripped = original.rstrip()
    if orig_stripped and orig_stripped[-1] in _TERMINAL_PUNCT:
        if not result or result[-1] not in _TERMINAL_PUNCT:
            result += orig_stripped[-1]
    return result


def normalize(text: str) -> str:
    original = text
    text = _cleanup(text)
    text = restore_yo(text)
    text = _replace_dates(text)
    text = _replace_year_ranges(text)
    text = _replace_years(text)
    text = _replace_centuries(text)
    text = _replace_labeled_romans(text)
    text = _replace_money(text)
    text = _replace_scale(text)
    text = _replace_percent(text)
    text = _replace_decimals(text)
    text = _replace_time(text)
    text = _replace_ordinal_digits(text)
    # Abbreviations/symbols before the generic cardinal pass: some of them
    # ("д." -> "дом" only before a number, arithmetic "+") key off a digit
    # still being a digit, which the cardinal pass would otherwise consume.
    text = _replace_abbreviations(text)
    text = _replace_symbols(text)
    text = _collapse_thousands_seps(text)
    text = _replace_bare_cardinals(text)
    text = _WS_RE.sub(" ", text).strip()
    text = _restore_terminal_punct(original, text)
    return _join_hyphenated(text)


# Qwen3-TTS reads a plain hyphen inside a word as a break ("что-то" -> "что… то"). Measured
# (bench/hyphen_test.py, 2 voices): gaps/sentence 1.48 with "-", 1.04 with a zero-width
# joiner, pronunciation unchanged per Whisper. Only letter-hyphen-letter; dashes keep pausing.
_INTRAWORD_HYPHEN_RE = re.compile(r"(?<=[А-Яа-яЁё])[-\u2010\u2011](?=[А-Яа-яЁё])")
ZWJ = "\u200d"


def _join_hyphenated(text: str) -> str:
    return _INTRAWORD_HYPHEN_RE.sub(ZWJ, text)
