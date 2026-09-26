"""Language detection by Cyrillic-vs-Latin letter ratio. Deliberately standalone
(no import of mytts.text, which is being built in parallel)."""
from __future__ import annotations

from mytts.contracts import Lang

_CYR_RANGE = ("Ѐ", "ӿ")
_LAT_RANGE = ("a", "z")


def detect_lang(text: str) -> Lang:
    cyr = lat = 0
    for ch in text:
        lo = ch.lower()
        if _CYR_RANGE[0] <= lo <= _CYR_RANGE[1]:
            cyr += 1
        elif _LAT_RANGE[0] <= lo <= _LAT_RANGE[1]:
            lat += 1
    return Lang.ru if cyr >= lat else Lang.en
