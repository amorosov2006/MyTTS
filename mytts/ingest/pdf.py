"""PDF parser: text-layer extraction with header/footer/footnote stripping and
de-hyphenation, falling back to on-device OCR (ocrmac / Apple Vision) for scans."""
from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import Path

import pymupdf

from mytts.contracts import Book, IngestError

from ._common import ChapterDraft, finalize_book
from ._lang import detect_lang
from mytts.contracts import Lang

_HYPHEN_JOIN_RE = re.compile(r"(\w+)-$")
_WORD_RE = re.compile(r"\w+(?:-\w+)*")
_FIRST_WORD_RE = re.compile(r"^(\w+)")
# particles / prefixes that are written with a hyphen in Russian ("что-то", "кое-кто")
_HYPHEN_SUFFIXES = {"то", "либо", "нибудь", "таки", "ка", "де", "с", "тка"}
_HYPHEN_PREFIXES = {"кое", "кой"}  # по-/из-/во- are far more often plain syllable breaks
_DIGIT_RE = re.compile(r"\d")
_OCR_LANGS = ["ru-RU", "en-US"]


class _Line:
    __slots__ = ("text", "size", "y", "page")

    def __init__(self, text: str, size: float, y: float, page: int):
        self.text = text
        self.size = size
        self.y = y
        self.page = page


def _extract_lines(page, page_no: int) -> list[_Line]:
    lines = []
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            size = max(s.get("size", 0.0) for s in spans)
            y = line.get("bbox", [0, 0, 0, 0])[1]
            lines.append(_Line(text, size, y, page_no))
    return lines


def _normalize_for_repeat(text: str) -> str:
    return _DIGIT_RE.sub("#", text.strip().lower())


def _is_pure_number(text: str) -> bool:
    return bool(re.fullmatch(r"[\-–—\s]*\d{1,4}[\-–—\s]*", text.strip()))


def _find_headers_footers(all_lines: list[_Line], page_height: float, n_pages: int,
                          body_size: float = 0.0) -> set[str]:
    """Running heads/feet: text in the top/bottom 10% band that repeats across pages. Books
    often alternate them (author on even pages, title on odd) or repeat the chapter title
    only within that chapter, so each variant may cover well under half of the pages."""
    top_thresh = page_height * 0.10
    bottom_thresh = page_height * 0.90
    seen_per_page: dict[str, set[int]] = {}
    for ln in all_lines:
        if body_size and ln.size > body_size * 1.15:
            continue  # big text near the top is a chapter heading, not a running head
        if ln.y <= top_thresh or ln.y >= bottom_thresh:
            key = _normalize_for_repeat(ln.text)
            seen_per_page.setdefault(key, set()).add(ln.page)
    need = 2 if n_pages <= 4 else max(3, int(n_pages * 0.1))
    return {key for key, pages in seen_per_page.items() if len(pages) >= need}


def _body_median_size(all_lines: list[_Line]) -> float:
    sizes = [round(l.size, 1) for l in all_lines]
    if not sizes:
        return 10.0
    return Counter(sizes).most_common(1)[0][0]


def _vocabulary(lines: list[str]) -> tuple[set[str], set[str]]:
    """Words seen inside lines (not split by a line break): plain words and hyphenated ones."""
    plain: set[str] = set()
    hyphenated: set[str] = set()
    for text in lines:
        body = _HYPHEN_JOIN_RE.sub("", text)  # the trailing broken fragment isn't a word
        for w in _WORD_RE.findall(body.lower()):
            (hyphenated if "-" in w else plain).add(w)
    return plain, hyphenated


def _keep_hyphen(head: str, tail: str, vocab: tuple[set[str], set[str]]) -> bool:
    """A line ends in "head-" and the next starts with "tail": real hyphen ("как-то",
    "кое-кто", "Санкт-Петербург") or a syllable break ("толь-ко")? The book's own vocabulary
    decides first; then Russian particle rules; default: syllable break."""
    plain, hyphenated = vocab
    h, t = head.lower(), tail.lower()
    if f"{h}-{t}" in hyphenated and f"{h}{t}" not in plain:
        return True
    if f"{h}{t}" in plain:
        return False
    if tail[:1].isupper():
        return True
    return t in _HYPHEN_SUFFIXES or (h in _HYPHEN_PREFIXES and f"{h}{t}" not in plain)


def _dehyphenate(lines: list[str], vocab: tuple[set[str], set[str]] | None = None) -> list[str]:
    vocab = vocab or _vocabulary(lines)
    out: list[str] = []
    for text in lines:
        m = _HYPHEN_JOIN_RE.search(out[-1]) if out else None
        first = _FIRST_WORD_RE.match(text) if text else None
        if m and first:
            head = m.group(1).split()[-1] if m.group(1) else ""
            if _keep_hyphen(head, first.group(1), vocab):
                out[-1] = out[-1] + text          # "как-" + "то ..." -> "как-то ..."
            else:
                out[-1] = out[-1][:-1] + text     # "толь-" + "ко ..." -> "только ..."
        else:
            out.append(text)
    return out


def _lines_to_paragraphs(lines: list[str], vocab: tuple[set[str], set[str]] | None = None) -> list[str]:
    """Join wrapped lines within a paragraph, breaking on a short trailing line
    (ends well before the margin) treated as a paragraph end. Simple heuristic:
    merge everything, since chapter bodies here are already one line per wrapped
    line with paragraph breaks unresolved from font metrics; use blank runs as
    hints when available, else keep a paragraph per contiguous run between short
    lines that are much shorter than the median."""
    if not lines:
        return []
    lines = _dehyphenate(lines, vocab)
    lengths = [len(l) for l in lines]
    median_len = sorted(lengths)[len(lengths) // 2] if lengths else 0
    paragraphs: list[str] = []
    cur: list[str] = []
    for i, text in enumerate(lines):
        cur.append(text)
        is_short = len(text) < median_len * 0.72 if median_len else False
        if is_short:
            paragraphs.append(" ".join(cur))
            cur = []
    if cur:
        paragraphs.append(" ".join(cur))
    return paragraphs


def _heading_size(kept: list[_Line], body: float) -> float | None:
    """Font size used for chapter headings when the PDF has no outline: the most frequent
    clearly-larger size among short lines that recurs on several pages (a one-off big line is
    the title page, not a chapter)."""
    sizes: dict[float, set[int]] = {}
    for ln in kept:
        if ln.size >= body * 1.4 and len(ln.text) <= 80:
            sizes.setdefault(round(ln.size, 1), set()).add(ln.page)
    good = [(len(pages), size) for size, pages in sizes.items() if 2 <= len(pages) <= 400]
    return max(good)[1] if good else None


def _heading_title(text: str, lang: Lang) -> str:
    m = re.fullmatch(r"(\d{1,3})\.?", text.strip())
    if m:
        return f"Глава {m.group(1)}" if lang == Lang.ru else f"Chapter {m.group(1)}"
    return text.strip()


def _split_by_font_headings(kept: list[_Line], size: float, vocab, lang: Lang) -> list[ChapterDraft]:
    drafts: list[ChapterDraft] = []
    title_parts: list[str] = []
    body: list[str] = []
    last_heading_page = None

    def flush(kind: str = "body") -> None:
        if title_parts or body:
            title = " ".join(title_parts)
            drafts.append(ChapterDraft(_heading_title(title, lang) if title else "",
                                       _lines_to_paragraphs(body, vocab), kind, kind == "body"))

    for ln in kept:
        if round(ln.size, 1) == size:
            if body or (title_parts and ln.page != last_heading_page):
                flush("body" if drafts or title_parts else "front")
                title_parts, body = [], []
            title_parts.append(ln.text)   # multi-line headings on one page merge into one title
            last_heading_page = ln.page
        else:
            body.append(ln.text)
    flush("body" if drafts or title_parts else "front")
    return [d for d in drafts if d.paragraphs or d.title]


def _is_scanned(doc) -> bool:
    total = len(doc)
    if total == 0:
        return False
    empty = sum(1 for page in doc if len(page.get_text().strip()) < 20)
    return empty / total > 0.5


def _ocr_page(page) -> list[str]:
    from ocrmac import ocrmac
    from PIL import Image

    pix = page.get_pixmap(dpi=250)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    ocr = ocrmac.OCR(img, language_preference=_OCR_LANGS)
    results = ocr.recognize()
    return [text for text, _conf, _bbox in results if text.strip()]


def _parse_scanned(doc, path: Path) -> Book:
    drafts: list[ChapterDraft] = []
    for i, page in enumerate(doc):
        lines = _ocr_page(page)
        paragraphs = _lines_to_paragraphs(lines)
        if paragraphs:
            drafts.append(ChapterDraft(f"Page {i + 1}", paragraphs, "body", True))
    if not drafts:
        raise IngestError("This scanned PDF produced no recognizable text (OCR found nothing).")
    meta = doc.metadata or {}
    book = finalize_book(
        title=(meta.get("title") or "").strip(),
        author=(meta.get("author") or "").strip() or None,
        source_path=str(path),
        source_format="pdf",
        cover=None,
        cover_mime=None,
        drafts=drafts,
        warnings=["Scanned PDF: text recognized with OCR, check the chapter preview."],
    )
    return book


def parse_book(path: Path) -> Book:
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise IngestError(f"This PDF file could not be read (corrupt file): {e}") from e

    if doc.is_encrypted:
        raise IngestError("This PDF is password-protected / DRM-locked and cannot be converted.")
    if len(doc) == 0:
        raise IngestError("This PDF has no pages.")

    if _is_scanned(doc):
        return _parse_scanned(doc, path)

    page_height = doc[0].rect.height
    all_lines: list[_Line] = []
    for i, page in enumerate(doc):
        all_lines.extend(_extract_lines(page, i))

    if not all_lines:
        raise IngestError("This PDF has no extractable text.")

    body_median = _body_median_size(all_lines)
    junk_keys = _find_headers_footers(all_lines, page_height, len(doc), body_median)

    kept: list[_Line] = []
    for ln in all_lines:
        key = _normalize_for_repeat(ln.text)
        is_heading_size = ln.size > body_median * 1.15
        if (key in junk_keys and not is_heading_size) or (_is_pure_number(ln.text) and not is_heading_size):
            continue
        if ln.size < body_median * 0.85 and (ln.y >= page_height * 0.7):
            continue  # small-font footnote near the bottom
        kept.append(ln)

    vocab = _vocabulary([ln.text for ln in kept])
    toc = doc.get_toc()
    if toc:
        level1 = [e for e in toc if e[0] == min(t[0] for t in toc)]
    else:
        level1 = []

    drafts: list[ChapterDraft]
    if len(level1) > 1:
        # split by page number using the outline, matching heading text on that page
        starts = [(e[2] - 1, e[1].strip()) for e in level1]  # 0-based page index
        starts.sort(key=lambda x: x[0])
        drafts = []
        for idx, (start_page, heading) in enumerate(starts):
            end_page = starts[idx + 1][0] if idx + 1 < len(starts) else len(doc)
            chunk = [ln for ln in kept if start_page <= ln.page < end_page]
            texts = [ln.text for ln in chunk]
            # drop the heading line itself (already captured as the title)
            texts = [t for t in texts if t.strip() != heading]
            drafts.append(ChapterDraft(heading, _lines_to_paragraphs(texts, vocab), "body", True))
    elif (hsize := _heading_size(kept, body_median)) is not None:
        lang = detect_lang(" ".join(ln.text for ln in kept[:2000]))
        drafts = _split_by_font_headings(kept, hsize, vocab, lang)
    else:
        texts = [ln.text for ln in kept]
        drafts = [ChapterDraft("", _lines_to_paragraphs(texts, vocab), "body", True)]

    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    author = (meta.get("author") or "").strip() or None

    if not drafts or not any(d.paragraphs for d in drafts):
        raise IngestError("This PDF has no extractable text.")

    return finalize_book(
        title=title,
        author=author,
        source_path=str(path),
        source_format="pdf",
        cover=None,
        cover_mime=None,
        drafts=drafts,
    )
