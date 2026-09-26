"""Golden table for English normalization: numbers, dates, abbreviations, roman numerals."""
import pytest

from bench.texts import TRAPS_EN
from mytts.text.normalize_en import normalize

CASES = [
    # dates
    (
        "On Jan. 5, 1891, Dr. Smith paid $2,500 for 3 houses on St. James St.",
        "On January fifth, eighteen ninety-one, Doctor Smith paid two thousand five hundred dollars "
        "for three houses on Saint James Street.",
    ),
    ("5 January 1891 was a long time ago.", "January fifth, eighteen ninety-one was a long time ago."),
    # years
    ("In eighteen ninety-one the family moved.", "In eighteen ninety-one the family moved."),
    ("In 1891 the family moved.", "In eighteen ninety-one the family moved."),
    ("She was born in 1905.", "She was born in nineteen oh five."),
    ("It happened in 2005.", "It happened in two thousand five."),
    ("By 2009 everything had changed.", "By two thousand nine everything had changed."),
    # decades
    ("The 1990s were a great decade.", "The nineteen nineties were a great decade."),
    ("The 1920s roared.", "The nineteen twenties roared."),
    # roman numerals
    ("Chapter IV. Part III.", "Chapter four. Part three."),
    ("World War II started in 1939.", "World War two started in nineteen thirty-nine."),
    ("Henry VIII married Elizabeth II.", "Henry the Eighth married Elizabeth the Second."),
    ("Book V of the series.", "Book five of the series."),
    # ordinal digits
    ("The 1st, 2nd, 3rd, 21st place.", "The first, second, third, twenty-first place."),
    # currency
    ("It costs $3.50.", "It costs three dollars and fifty cents."),
    ("It costs $2,500.", "It costs two thousand five hundred dollars."),
    ("It costs £20.", "It costs twenty pounds."),
    ("It costs €50.", "It costs fifty euros."),
    # percent / decimals
    ("15% off.", "fifteen percent off."),
    ("3.5% inflation.", "three point five percent inflation."),
    ("Pi is about 3.14.", "Pi is about three point one four."),
    # time
    ("Meet at 10:30.", "Meet at ten thirty."),
    ("Also at 10:05.", "Also at ten oh five."),
    ("Also at 10:00.", "Also at ten o'clock."),
    ("Call at 5 p.m.", "Call at five p m."),
    ("Call at 5 a.m.", "Call at five a m."),
    # abbreviations
    ("See Mr. Smith and Mrs. Jones.", "See Mister Smith and Missus Jones."),
    ("Ask Ms. Lee or Dr. Brown.", "Ask Miss Lee or Doctor Brown."),
    ("For e.g. this and i.e. that and etc.", "For for example this and that is that and et cetera."),
    ("Compare X vs. Y.", "Compare X versus Y."),
    ("No. 5 is the answer.", "Number five is the answer."),
    ("Prof. Gray and Gen. Lee and Capt. Hook.", "Professor Gray and General Lee and Captain Hook."),
    ("Lt. Dan and John Jr. and Mary Sr.", "Lieutenant Dan and John Junior and Mary Senior."),
    ("On Mon. and Tue. we leave.", "On Monday and Tuesday we leave."),
    ("See the Jan. issue.", "See the January issue."),
    # pronoun "I" and homographs must be left alone
    ("I read the book yesterday; now I read the news.", "I read the book yesterday; now I read the news."),
    ("The wind wound around the tower.", "The wind wound around the tower."),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_normalize_en_golden(text, expected):
    assert normalize(text) == expected


def test_normalize_en_idempotent():
    for text, expected in CASES:
        assert normalize(expected) == expected


@pytest.mark.parametrize("text", TRAPS_EN)
def test_normalize_en_traps_dont_crash(text):
    result = normalize(text)
    assert result
    assert not result.isspace()


def test_traps_en_date_money_abbrev_trap():
    result = normalize(TRAPS_EN[0])
    assert "eighteen ninety-one" in result
    assert "two thousand five hundred dollars" in result
    assert "Doctor Smith" in result
    assert "Saint James Street" in result
    assert "1891" not in result
    assert "2500" not in result


def test_traps_en_pronoun_i_and_homograph_untouched():
    result = normalize(TRAPS_EN[1])
    assert result == TRAPS_EN[1]
