"""Chapter -> list[Segment]: footnote stripping, sentence splitting, packing, titles.

Design choice (documented per the task): sentences are split from the
ORIGINAL paragraph text FIRST, and each resulting sentence is normalized
independently, rather than normalizing the whole paragraph and splitting the
result. Reasons:
  - razdel.sentenize (ru) and pysbd (en) are both reasonably abbreviation-
    aware already (verified against т.е./руб./ул./д./Mr./Dr./St./e.g./i.e.
    and initials like "А. С. Пушкин" -- none of these cause a false split).
  - Splitting first gives a trivial, exact mapping from Segment.source (the
    original sentence) to Segment.text (its normalized form): no need to
    track character offsets between two strings whose lengths and word
    counts don't correspond ("2500 руб." -> "две тысячи пятьсот рублей").

Known gap: an abbreviation that also happens to end a real sentence can
still make the splitter merge two sentences into one ("...on St. James St.
He was happy." -> pysbd keeps "St." from splitting, so this becomes one
"sentence"). The merged text is still handled correctly by the overlong-
sentence splitter below; only pause placement may be a beat off in that rare
case.
"""
from __future__ import annotations

import re

import pysbd
import razdel

from mytts.config import (
    PAUSE_CHAPTER_TITLE_MS,
    PAUSE_PARAGRAPH_MS as _DEFAULT_PAUSE_PARAGRAPH_MS,
    PAUSE_SENTENCE_MS as _DEFAULT_PAUSE_SENTENCE_MS,
    SEGMENT_MAX_CHARS,
    SEGMENT_TARGET_CHARS,
)
from mytts.contracts import Chapter, Lang, Segment
from mytts.text import numbers as num
from mytts.text.lang import detect_lang
from mytts.text.normalize_en import normalize as _normalize_en
from mytts.text.normalize_en import num2words as _en_num2words
from mytts.text.normalize_ru import normalize as _normalize_ru

_MIN_FRAGMENT_CHARS = 25

_en_segmenter = pysbd.Segmenter(language="en", clean=False)


def normalize(text: str, lang: Lang) -> str:
    """text/__init__.py's documented `normalize`; lives here to avoid a
    circular import with prepare_chapter (both live in this module)."""
    return _normalize_ru(text) if lang == Lang.ru else _normalize_en(text)


# ----------------------------------------------------------------------------- footnotes

_FOOTNOTE_PATTERNS = [
    re.compile(r"\[\d{1,3}\]"),           # [12]
    re.compile(r"\[\*+\]"),               # [*]
    re.compile(r"\{\d{1,3}\}"),           # {12}
    re.compile(r"(?<=\w)[⁰¹²³⁴-⁹]+"),  # trailing superscript digits
    re.compile(r"(?<=\w)\*+(?!\w)"),      # a bare * stuck to the end of a word
    re.compile(r"(?<=\w)\(\d{1,3}\)"),    # a bare (1) stuck to the end of a word, no space
]
_WS_RE = re.compile(r"[ \t]+")


def strip_footnotes(text: str) -> str:
    for pattern in _FOOTNOTE_PATTERNS:
        text = pattern.sub("", text)
    return _WS_RE.sub(" ", text).strip()


_SCENE_SEPARATOR_RE = re.compile(r"^[\*⁂•·\s]+$")


def is_scene_separator(paragraph: str) -> bool:
    stripped = paragraph.strip()
    return bool(stripped) and bool(_SCENE_SEPARATOR_RE.match(stripped))


# ----------------------------------------------------------------------------- sentence split

def split_sentences(text: str, lang: Lang) -> list[str]:
    if lang == Lang.ru:
        return [s.text.strip() for s in razdel.sentenize(text) if s.text.strip()]
    return [s.strip() for s in _en_segmenter.segment(text) if s.strip()]


_OVERLONG_SPLIT_RE = re.compile(r"[;:—,]")


def _split_overlong(text: str, max_chars: int) -> list[str]:
    """Cut an over-long normalized sentence at ; : — , nearest the middle,
    recursing until every piece fits; falls back to a whitespace split."""
    if len(text) <= max_chars:
        return [text]
    mid = len(text) / 2
    candidates = [m for m in _OVERLONG_SPLIT_RE.finditer(text) if 0 < m.end() < len(text)]
    if candidates:
        best = min(candidates, key=lambda m: abs(m.start() - mid))
        left, right = text[: best.end()].strip(), text[best.end():].strip()
        if left and right:
            return _split_overlong(left, max_chars) + _split_overlong(right, max_chars)
    words = text.split(" ")
    if len(words) > 1:
        best_i, best_diff = 1, None
        acc = 0
        for i in range(1, len(words)):
            acc = len(" ".join(words[:i]))
            diff = abs(acc - mid)
            if best_diff is None or diff < best_diff:
                best_diff, best_i = diff, i
        left, right = " ".join(words[:best_i]).strip(), " ".join(words[best_i:]).strip()
        if left and right:
            return _split_overlong(left, max_chars) + _split_overlong(right, max_chars)
    return [text]  # single unsplittable token (e.g. a URL) -- let it through over max


def _prepare_sentence_pairs(paragraph: str, lang: Lang, max_chars: int) -> list[tuple[str, str]]:
    """(source, normalized_text) pairs for one paragraph, each text <= max_chars."""
    pairs: list[tuple[str, str]] = []
    for raw in split_sentences(paragraph, lang):
        normalized = normalize(raw, lang)
        if not normalized or not re.search(r"\w", normalized):
            continue  # drop empty / punctuation-only sentences (e.g. a lone footnote marker)
        if len(normalized) <= max_chars:
            pairs.append((raw, normalized))
        else:
            # Can't cleanly align sub-splits of the normalized text back to a
            # slice of the (differently-shaped) original -- all sub-segments
            # keep the whole original sentence as `source`. Documented gap.
            for piece in _split_overlong(normalized, max_chars):
                pairs.append((raw, piece))
    return pairs


# ----------------------------------------------------------------------------- packing

def _pack_pairs(pairs: list[tuple[str, str]], target: int, max_chars: int) -> list[tuple[str, str]]:
    packed: list[tuple[str, str]] = []
    cur_src: list[str] = []
    cur_txt: list[str] = []
    cur_len = 0
    for src, txt in pairs:
        add_len = len(txt) + (1 if cur_txt else 0)
        if cur_txt and cur_len + add_len > target:
            packed.append((" ".join(cur_src), " ".join(cur_txt)))
            cur_src, cur_txt, cur_len = [], [], 0
            add_len = len(txt)
        cur_src.append(src)
        cur_txt.append(txt)
        cur_len += add_len
    if cur_txt:
        packed.append((" ".join(cur_src), " ".join(cur_txt)))
    return packed


def _merge_tiny_pairs(packed: list[tuple[str, str]], max_chars: int, min_chars: int) -> list[tuple[str, str]]:
    result = list(packed)
    i = 0
    while i < len(result):
        src, txt = result[i]
        if len(txt) < min_chars and len(result) > 1:
            if i + 1 < len(result) and len(txt) + 1 + len(result[i + 1][1]) <= max_chars:
                nsrc, ntxt = result[i + 1]
                result[i + 1] = (f"{src} {nsrc}", f"{txt} {ntxt}")
                del result[i]
                continue
            if i > 0 and len(result[i - 1][1]) + 1 + len(txt) <= max_chars:
                psrc, ptxt = result[i - 1]
                result[i - 1] = (f"{psrc} {src}", f"{ptxt} {txt}")
                del result[i]
                i -= 1
                continue
        i += 1
    return result


# ----------------------------------------------------------------------------- titles

_RU_ARABIC_LABEL_RE = re.compile(
    r"^(Глава|глава|Часть|часть|Том|том|Книга|книга|Раздел|раздел)\s+(\d+)\b"
)
_RU_LABEL_GENDER = {"глава": "f", "часть": "f", "том": "m", "книга": "f", "раздел": "m"}
_BARE_ROMAN_RE = re.compile(r"^[IVXLCDMivxlcdm]+$")


def expand_ru_arabic_chapter_label(text: str) -> str:
    """"Глава 2" -> "Глава вторая" (arabic digit right after a chapter/part/
    ...  label, at the START of the text, agreeing in gender with the
    label). Roman numerals in the same spot ("Глава II") already get this
    from normalize_ru's own _replace_labeled_romans, and EN's "Chapter 4"/
    "Chapter IV" both already come out as "Chapter four" via the ordinary
    normalize_en pipeline -- this arabic-digit/RU combination is the one gap
    that needs a dedicated pass, used by both normalize_title (so titles are
    read this way) and compare_form (so a QA transcript that writes the
    title as "Глава 2" still canonicalizes to the same words as the spoken
    "Глава вторая", instead of comparing against a plain cardinal "два").
    """

    def sub(m: re.Match) -> str:
        label, digits = m.group(1), m.group(2)
        gender = _RU_LABEL_GENDER[label.lower()]
        return f"{label} {num.ru_ordinal(int(digits), gender=gender)}"

    return _RU_ARABIC_LABEL_RE.sub(sub, text)


def normalize_title(title: str, lang: Lang) -> str:
    title = title.strip()
    if not title:
        return ""
    if _BARE_ROMAN_RE.match(title):
        n = num.roman_to_int(title)
        if n is not None:
            return num.ru_cardinal(n) if lang == Lang.ru else _en_num2words(n)
    if lang == Lang.ru:
        title = expand_ru_arabic_chapter_label(title)
        return normalize(title, Lang.ru)
    # EN: "Chapter 4" already becomes "Chapter four" via the ordinary bare-
    # cardinal pass in normalize_en, and "Chapter IV" via its roman-numeral
    # pass -- no extra label-specific handling needed here.
    return normalize(title, Lang.en)


# ----------------------------------------------------------------------------- entry point

def _segment_lang(text: str, book_lang: Lang) -> Lang:
    if not re.search(r"[a-zA-Zа-яёА-ЯЁ]", text):
        return book_lang  # no letters at all (numbers/symbols only): can't detect, trust the book
    return detect_lang(text)


def prepare_chapter(
    chapter: Chapter,
    lang: Lang,
    *,
    read_title: bool = True,
    skip_footnotes: bool = True,
    pause_sentence_ms: int = _DEFAULT_PAUSE_SENTENCE_MS,
    pause_paragraph_ms: int = _DEFAULT_PAUSE_PARAGRAPH_MS,
    target_chars: int = SEGMENT_TARGET_CHARS,
    max_chars: int = SEGMENT_MAX_CHARS,
) -> list[Segment]:
    segments: list[Segment] = []
    index = 0

    if read_title and chapter.title.strip():
        title_text = normalize_title(chapter.title, lang)
        if title_text.strip():
            segments.append(Segment(
                id=f"c{chapter.index:03d}s{index:04d}",
                chapter=chapter.index,
                index=index,
                source=chapter.title,
                text=title_text,
                lang=lang,
                pause_after_ms=PAUSE_CHAPTER_TITLE_MS,
                is_title=True,
            ))
            index += 1

    for paragraph in chapter.paragraphs:
        text = strip_footnotes(paragraph) if skip_footnotes else paragraph.strip()
        if not text:
            continue
        if is_scene_separator(text):
            if segments:
                segments[-1].pause_after_ms = max(segments[-1].pause_after_ms, pause_paragraph_ms * 2)
            continue

        seg_lang = _segment_lang(text, lang)
        pairs = _prepare_sentence_pairs(text, seg_lang, max_chars)
        if not pairs:
            continue
        packed = _pack_pairs(pairs, target_chars, max_chars)
        packed = _merge_tiny_pairs(packed, max_chars, _MIN_FRAGMENT_CHARS)

        for i, (src, txt) in enumerate(packed):
            is_last = i == len(packed) - 1
            segments.append(Segment(
                id=f"c{chapter.index:03d}s{index:04d}",
                chapter=chapter.index,
                index=index,
                source=src,
                text=txt,
                lang=seg_lang,
                pause_after_ms=pause_paragraph_ms if is_last else pause_sentence_ms,
            ))
            index += 1

    return segments
