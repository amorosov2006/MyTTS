"""Restore 'ё' in Russian text written with plain 'е' (common in books/typesetting).

Correctness over coverage: only unambiguous words/stems go in the table. Words
that are genuine е/ё minimal pairs (все/всё, небо/нёбо, ее/её as a possessive
vs. "her" pronoun contexts, передохнет/передохнёт, узнает/узнаёт-style aspect
pairs) are intentionally left out — restoring them wrong is worse than leaving
them alone, and Qwen3-TTS ignores stress marks anyway so we can't disambiguate
via stress.

Matching is stem-based (a whole-word regex per stem+known endings) so common
inflections are covered without listing every form by hand, and capitalization
of the original word is preserved.
"""
from __future__ import annotations

import re

# (yo_stem, ye_stem) pairs applied with a shared adjective-ending set below.
# лёгкий is handled separately (in _WHOLE_WORDS): the -к stem takes -ий/-им/-ие
# endings, not -ый/-ым/-ые (the usual spelling-rule exception after к/г/х).
_STEM_PAIRS: list[tuple[str, str]] = [
    ("тёмн", "темн"),        # тёмный, тёмная, тёмное, тёмные, тёмного...
    ("зелён", "зелен"),      # зелёный, зелёная...
    ("чёрн", "черн"),        # чёрный...
    ("жёлт", "желт"),        # жёлтый...
    ("тёпл", "тепл"),        # тёплый...
    ("твёрд", "тверд"),      # твёрдый...
    ("тяжёл", "тяжел"),      # тяжёлый...
    ("удивлён", "удивлен"),  # удивлён, удивлённый
    ("влюблён", "влюблен"),  # влюблён, влюблённый
    ("рождён", "рожден"),    # рождён, рождённый
]

_ADJ_ENDINGS = ["ый", "ого", "ому", "ым", "ом", "ая", "ой", "ую", "ое",
                "ые", "ых", "ыми", "енький", "енькая", "енькое", ""]

# Patronymics (Семёнович/Семёновна, Фёдорович/Фёдоровна, Артёмович/Артёмовна):
# stem = the first name itself, unambiguous ("-ович"/"-овна" isn't a real word
# on its own), so the full paradigm is safe to restore.
_PATRONYMIC_PAIRS: list[tuple[str, str]] = [
    ("семён", "семен"),
    ("фёдор", "федор"),
    ("артём", "артем"),
]
_PATRONYMIC_ENDINGS = ["ович", "овича", "овичу", "овичем", "овиче",
                       "овна", "овны", "овне", "овну", "овной"]

# key = е-spelled form as commonly typeset (input), value = correct ё form (output).
_WHOLE_WORDS: dict[str, str] = {
    # pronouns / particles
    "еще": "ещё",
    # легкий (spelling-rule exception after к: -ий/-им/-ие, not -ый/-ым/-ые)
    "легкий": "лёгкий", "легкого": "лёгкого", "легкому": "лёгкому", "легким": "лёгким",
    "легком": "лёгком", "легкая": "лёгкая", "легкой": "лёгкой", "легкую": "лёгкую",
    "легкое": "лёгкое", "легкие": "лёгкие", "легких": "лёгких", "легкими": "лёгкими",
    # "легко" (adverb) keeps е: stress falls on the final о, so the root is unstressed.
    # verbs (3rd person present, past tense masc, etc.)
    "идет": "идёт", "идем": "идём", "идете": "идёте",
    "живет": "живёт", "живем": "живём", "живете": "живёте",
    "пришел": "пришёл",
    "нашел": "нашёл", "ушел": "ушёл", "вошел": "вошёл", "перешел": "перешёл",
    "пошел": "пошёл", "зашел": "зашёл", "подошел": "подошёл", "обошел": "обошёл",
    "дошел": "дошёл", "разошелся": "разошёлся",
    "несет": "несёт", "несем": "несём",
    "везет": "везёт",
    "берет": "берёт", "берем": "берём",
    "дает": "даёт", "даем": "даём",
    "поет": "поёт", "поем": "поём",
    "льет": "льёт",
    "пьет": "пьёт", "пьем": "пьём",
    "ждет": "ждёт", "ждем": "ждём",
    "плывет": "плывёт",
    "растет": "растёт",
    "жует": "жуёт",
    "клюет": "клюёт",
    "мнет": "мнёт",
    "трет": "трёт",
    # nouns
    "ребенок": "ребёнок", "ребенка": "ребёнка", "ребенку": "ребёнку", "ребенком": "ребёнком",
    "береза": "берёза", "березы": "берёзы", "березу": "берёзу", "березе": "берёзе",
    "березой": "берёзой", "березок": "берёзок",
    "звезды": "звёзды", "звезд": "звёзд", "звездами": "звёздами", "звездный": "звёздный",
    "слезы": "слёзы", "слез": "слёз", "слезами": "слёзами",
    "мед": "мёд", "меду": "мёду",
    "лед": "лёд",
    "мертвый": "мёртвый", "мертвая": "мёртвая", "мертвые": "мёртвые",
    "полет": "полёт", "полета": "полёта", "полеты": "полёты",
    "костер": "костёр",
    "щеки": "щёки", "щеку": "щёку",
    "пчелы": "пчёлы", "пчел": "пчёл",
    "орел": "орёл",
    "осел": "осёл",
    "котел": "котёл",
    "жены": "жёны",
    "сестры": "сёстры",
    # names and patronymics (unambiguous)
    # Пётр is irregular (a "fleeting vowel" name): the ё only appears in the
    # nominative -- Петра/Петру/Петром/Петре have NO ё, so ONLY the bare
    # nominative form is restored here.
    "петр": "пётр",
    # Семён/Фёдор/Артём decline regularly and keep ё in every case.
    "семен": "семён", "семена": "семёна", "семену": "семёну",
    "семеном": "семёном", "семене": "семёне",
    "федор": "фёдор", "федора": "фёдора", "федору": "фёдору",
    "федором": "фёдором", "федоре": "фёдоре",
    "артем": "артём", "артема": "артёма", "артему": "артёму",
    "артемом": "артёмом", "артеме": "артёме",
    "алена": "алёна", "алены": "алёны", "алене": "алёне",
    "алену": "алёну", "аленой": "алёной",
    "лева": "лёва", "левы": "лёвы", "леве": "лёве", "леву": "лёву", "левой": "лёвой",
    # "лени" (genitive of "лень", laziness) is a real, common word -- only
    # the nominative "Леня" is restored, not the rest of the paradigm.
    "леня": "лёня",
    "потемкин": "потёмкин", "потемкина": "потёмкина", "потемкину": "потёмкину",
    "потемкиным": "потёмкиным", "потемкине": "потёмкине",
    # "королева" (queen) is a real, common word -- only the nominative
    # surname "Королёв" is restored, not the rest of the paradigm.
    "королев": "королёв",
    "хрущев": "хрущёв", "хрущева": "хрущёва", "хрущеву": "хрущёву",
    "хрущевым": "хрущёвым", "хрущеве": "хрущёве",
    "горбачев": "горбачёв", "горбачева": "горбачёва", "горбачеву": "горбачёву",
    "горбачевым": "горбачёвым", "горбачеве": "горбачёве",
    # NOT restored (ambiguous minimal pairs): все/всё, небо/нёбо.
}


def _build_stem_patterns(pairs: list[tuple[str, str]], endings: list[str]) -> list[tuple[re.Pattern, str]]:
    patterns = []
    for yo_stem, ye_stem in pairs:
        for ending in endings:
            yo_word = yo_stem + ending
            ye_word = ye_stem + ending
            if not ye_word:
                continue
            patterns.append((re.compile(r"\b" + ye_word + r"\b", re.IGNORECASE), yo_word))
    return patterns


_STEM_PATTERNS = (
    _build_stem_patterns(_STEM_PAIRS, _ADJ_ENDINGS)
    + _build_stem_patterns(_PATRONYMIC_PAIRS, _PATRONYMIC_ENDINGS)
)
_WORD_RE = re.compile(r"\b[а-яё]+\b", re.IGNORECASE)


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def restore_yo(text: str) -> str:
    """Replace known е->ё words/stems. Conservative: only ~150-300 unambiguous forms."""

    def word_sub(m: re.Match) -> str:
        word = m.group(0)
        lower = word.lower()
        if lower in _WHOLE_WORDS:
            return _match_case(word, _WHOLE_WORDS[lower])
        return word

    text = _WORD_RE.sub(word_sub, text)

    for pattern, yo_word in _STEM_PATTERNS:
        def sub(m: re.Match, yo_word=yo_word) -> str:
            return _match_case(m.group(0), yo_word)

        text = pattern.sub(sub, text)

    return text
