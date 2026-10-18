"""OCR text: restore the space typewritten scans lose after punctuation, without touching math, numbers or abbreviations."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.ocr_extractor import restore_punctuation_spacing as fix


@pytest.mark.parametrize("raw,expected", [
    ("votes than Taft and Wilson combined received.I hereby", "votes than Taft and Wilson combined received. I hereby"),
    ("thousands will greet you,and in no uncertain tones.", "thousands will greet you, and in no uncertain tones."),
    ("the Republican party,the party of", "the Republican party, the party of"),
    ("Indiana,Pa.", "Indiana, Pa."),
    ("Dear Sir:Indiana", "Dear Sir: Indiana"),
])
def test_missing_space_after_punctuation_is_restored(raw, expected):
    assert fix(raw) == expected


@pytest.mark.parametrize("text", ["x,y and a,b", "1,000 and 3.5 and 12:30", "U.S.A and Ph.D.", "www.Google.com", "http://a.b/c,d",
                                  "f(x,y)=ax+b", "Mr.Smith's", "e.g.", "Iam,"])
def test_variables_numbers_urls_and_abbreviations_are_left_alone(text):
    assert fix(text) == text
