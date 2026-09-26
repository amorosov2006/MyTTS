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
    # NOT restored (ambiguous minimal pairs): все/всё, небо/нёбо.
}


def _build_stem_patterns() -> list[tuple[re.Pattern, str]]:
    patterns = []
    for yo_stem, ye_stem in _STEM_PAIRS:
        for ending in _ADJ_ENDINGS:
            yo_word = yo_stem + ending
            ye_word = ye_stem + ending
            if not ye_word:
                continue
            patterns.append((re.compile(r"\b" + ye_word + r"\b", re.IGNORECASE), yo_word))
    return patterns


_STEM_PATTERNS = _build_stem_patterns()
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
