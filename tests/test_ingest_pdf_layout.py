"""PDF layouts seen in real books: alternating running heads, chapter headings only by font
(no outline), and hyphenated words at line ends ("как-то" vs syllable breaks "толь-ко")."""
import glob

import pytest
from reportlab.lib.pagesizes import A6
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from mytts.ingest import parse_book

FONT = next(iter(glob.glob("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")), None)

# (line text, is_last_line_of_paragraph) — line ends carry the hyphenation cases under test
CHAPTER_LINES = [
    "Она долго стояла у окна и смотрела, как",
    "снег засыпает двор. Надо было что-",
    "то делать, но никто не знал, что имен-",
    "но. Кое-",
    "кто уже спал, а как-",
    "нибудь потом всё решится само.",
    "Только ветер стучал в ставни, и толь-",
    "ко часы тикали на стене в тишине.",
]


def _make_pdf(path):
    pdfmetrics.registerFont(TTFont("AU", FONT))
    c = canvas.Canvas(str(path), pagesize=A6)
    w, h = A6
    page = 0
    for chapter in range(1, 4):
        for part in range(2):  # two pages per chapter
            page += 1
            c.setFont("AU", 7)
            c.drawString(30, h - 20, "Иван Петров" if page % 2 == 0 else "ТИХАЯ СТАНЦИЯ")
            c.drawCentredString(w / 2, 15, str(page))
            y = h - 50
            if part == 0:
                c.setFont("AU", 22)
                c.drawString(30, y, f"{chapter}.")
                y -= 34
            c.setFont("AU", 9)
            for line in CHAPTER_LINES:
                c.drawString(30, y, line)
                y -= 12
            c.showPage()
    c.save()


@pytest.mark.skipif(FONT is None, reason="needs a Cyrillic TTF")
def test_pdf_book_layout(tmp_path):
    pdf = tmp_path / "book.pdf"
    _make_pdf(pdf)
    book = parse_book(pdf)
    body = [c for c in book.chapters if c.include]
    assert [c.title for c in body] == ["Глава 1", "Глава 2", "Глава 3"]
    text = "\n".join(p for c in body for p in c.paragraphs)
    assert "ТИХАЯ СТАНЦИЯ" not in text and "Иван Петров" not in text   # alternating heads
    assert "что-то" in text and "Кое-кто" in text and "как-нибудь" in text  # real hyphens kept
    assert "именно" in text and "только часы" in text                     # syllable breaks joined
    assert "чтото" not in text and "как- " not in text
