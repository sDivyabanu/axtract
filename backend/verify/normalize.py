"""AXTRACT Verify: text and number normalisation for content comparison.

Goal: make two renderings of the SAME content compare equal, without ever making two
DIFFERENT contents compare equal. When in doubt, a difference is kept.

What is folded (formatting only):
  * Unicode composition (NFC), zero-width characters, soft hyphens, odd space characters
  * ligatures (ﬁ -> fi), line-break variants, typographic quotes (optional)
  * hyphenation at line ends (heuristic; see `hyphen_variants` for the ambiguous case)
  * case (optional), runs of whitespace
  * number formatting: thousands separators, currency position, accounting negatives,
    percent spelling, "M"/"million" scale words

What is deliberately NOT folded:
  * NFKC compatibility mapping: it would turn x² into x2 and ½ into 1⁄2
  * different digits, signs, scales, currencies or units: $18.2M != $13.2M, 5% != 5, 007 != 7
  * dashes (optional), letters that merely look alike, identifiers with leading zeros
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
}
_ZERO_WIDTH = re.compile("[​⁠﻿]")  # NOT ZWJ/ZWNJ: they change shaping in some scripts
_ODD_SPACES = re.compile("[\u00a0  - \u202f 　]")
_LINE_BREAKS = re.compile("\r\n|[\r  \u0085\x0b\x0c]")
_CONTROLS = re.compile("[\x00-\x08\x0e-\x1f\x7f]")
_SOFT_HYPHEN_BREAK = re.compile("­[ \t]*\n[ \t]*")
_HYPHEN_BREAK = re.compile(r"([^\W\d_])-[ \t]*\n[ \t]*(\S)")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                         "“": '"', "”": '"', "„": '"', "‟": '"'})
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-"})


def _dehyphenate(text: str, join: bool) -> str:
    """Resolve 'word-\\nword'. A hyphen followed by a lowercase letter is a line-break hyphen
    and is dropped when `join` is true; before an uppercase letter or digit it is kept
    ('Tel-\\nAviv' -> 'Tel-Aviv', 'COVID-\\n19' -> 'COVID-19'). Heuristic: 'e-\\nmail' becomes 'email'."""

    def repl(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        return left + right if join and right.islower() else f"{left}-{right}"

    return _HYPHEN_BREAK.sub(repl, text)


from utils.request_cache import memo_text


@memo_text
def normalize_text(
    text: str,
    *,
    case_sensitive: bool = True,
    dehyphenate: bool = True,
    fold_ligatures: bool = True,
    fold_quotes: bool = True,
    fold_dashes: bool = False,
    preserve_lines: bool = False,
    canonical_numbers: bool = False,
    locale: Literal["auto", "en", "eu"] = "auto",
) -> str:
    """Normalise text for comparison. Idempotent."""
    if not text:
        return ""
    s = unicodedata.normalize("NFC", text)
    s = _CONTROLS.sub("", s)
    s = _ZERO_WIDTH.sub("", s)
    s = _LINE_BREAKS.sub("\n", s)
    s = _SOFT_HYPHEN_BREAK.sub("", s).replace("­", "")
    if canonical_numbers:  # before spaces are folded: NBSP / thin spaces still mark thousands groups
        s = canonicalize_numbers(s, locale=locale)
    if fold_ligatures:
        s = s.translate({ord(k): v for k, v in _LIGATURES.items()})
    s = _ODD_SPACES.sub(" ", s)
    if dehyphenate:
        s = _dehyphenate(s, join=True)
    else:
        s = _dehyphenate(s, join=False)
    if fold_quotes:
        s = s.translate(_QUOTES)
    if fold_dashes:
        s = s.translate(_DASHES)
    if preserve_lines:
        lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n"))
        s = "\n".join(ln for ln in lines if ln)
    else:
        s = re.sub(r"\s+", " ", s).strip()
    if not case_sensitive:
        s = s.casefold()
    return s


def hyphen_variants(text: str) -> tuple[str, str]:
    """Both readings of end-of-line hyphens: (joined, kept). A comparison should accept a
    match against either, because 'e-\\nmail' vs 'inter-\\nnational' cannot be told apart."""
    base = normalize_text(text, dehyphenate=True)
    kept = normalize_text(_dehyphenate(unicodedata.normalize("NFC", text), join=False), dehyphenate=False)
    return base, kept


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_CUR_SYMBOLS = "$€£¥₹₩₽₺₫฿₪₱"
_CUR_CODES = (
    "USD EUR GBP JPY CNY RMB INR CHF CAD AUD NZD SEK NOK DKK KRW BRL MXN ZAR SGD HKD AED SAR RUB TRY VND THB ILS PHP"
).split()
# Only unambiguous pairs are merged. '$' and '¥' stay distinct from every code (USD/CAD/AUD, JPY/CNY).
_CODE_TO_SYMBOL = {"EUR": "€", "GBP": "£", "INR": "₹", "KRW": "₩", "RUB": "₽", "TRY": "₺",
                   "VND": "₫", "THB": "฿", "ILS": "₪", "PHP": "₱"}
_SCALE_WORDS = {"thousand": 3, "million": 6, "billion": 9, "trillion": 12}
_SCALE_ABBR = {"k": 3, "m": 6, "mm": 6, "mn": 6, "b": 9, "bn": 9, "t": 12, "tn": 12}
_MINUS_LIKE = str.maketrans({"\u2212": "-", "‒": "-", "–": "-", "﹣": "-", "－": "-", "％": "%"})

_CODES_RE = "|".join(_CUR_CODES)
_BODY = r"(?:\d[\d.,' \u00a0\u2009\u202f]*\d|\d|[.,]\d+)"
_PARSE_RE = re.compile(
    rf"""^\s*
    (?P<s1>[+\-])?\s*
    (?:(?P<c1>[{re.escape(_CUR_SYMBOLS)}])|(?P<k1>{_CODES_RE})\s?)?\s*
    (?P<s2>[+\-])?\s*
    (?P<body>{_BODY})
    (?:\s*(?P<scale>thousand|million|billion|trillion|[A-Za-z]{{1,2}}))?
    (?:\s*(?P<pct>%|percent|pct|per\s?cent))?
    (?:\s*(?P<k2>{_CODES_RE}|[{re.escape(_CUR_SYMBOLS)}]))?
    \s*(?P<s3>-)?\s*$""",
    re.X | re.I,
)
_INDIAN = re.compile(r"^\d{1,2}(,\d{2})+,\d{3}$")


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal  # signed, scale applied
    unit: str  # "", "%", or a currency token
    decimals: int  # digits after the decimal marker as written (before scaling)
    scale: int  # power of ten applied by a K/M/B/T/word suffix, 0 if none
    negative_style: str  # "", "minus", "parens", "trailing"
    identifier_like: bool  # integer part has leading zeros (e.g. "007"): treat as a code, not a quantity
    ambiguous: bool  # the thousands/decimal reading was a guess ("1,234" / "1.234")
    digit_string: str  # digits exactly as written, separators removed
    raw: str

    def canonical(self, keep_precision: bool = True) -> str:
        """One spelling per quantity. Written decimals are kept ("1.50") unless a scale suffix
        was used, where trailing zeros carry no information ("18.20M" == "18.2M")."""
        v = abs(self.value)
        if self.identifier_like:  # "007" is a code, not 7: keep its digits exactly
            ds, d = self.digit_string, self.decimals
            body = ds[: len(ds) - d] + (f".{ds[-d:]}" if d else "")
        else:
            body = f"{v:.{self.decimals}f}" if keep_precision and self.scale == 0 else format(v.normalize(), "f")
        sign = "-" if self.value < 0 else ""
        return f"{sign}{body}%" if self.unit == "%" else f"{sign}{self.unit}{body}"


def _to_ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.decimal(c)) if unicodedata.category(c) == "Nd" else c for c in s)


def _groups_ok(groups: list[str], sep: str) -> bool:
    if len(groups) < 2:
        return True
    if not all(g.isdigit() for g in groups):
        return False
    first, rest = groups[0], groups[1:]
    if 1 <= len(first) <= 3 and all(len(g) == 3 for g in rest):
        return True
    return sep == "," and bool(_INDIAN.match(",".join(groups)))


def _interpret(body: str, locale: str) -> tuple[str, str, bool, bool] | None:
    """Return (integer digits, fraction digits, ambiguous, used_thousands_separator)."""
    body = body.strip()
    if re.search(r"['\s\u00a0\u2009\u202f]", body):  # space/apostrophe separators are always thousands
        parts = re.split(r"['\s\u2009\u202f]+", body)
        last = re.fullmatch(r"(\d+)(?:[.,](\d+))?", parts[-1])
        if not last or not all(p.isdigit() for p in parts[:-1]):
            return None
        groups = parts[:-1] + [last.group(1)]
        if not _groups_ok(groups, "'"):
            return None
        return "".join(groups), last.group(2) or "", False, True

    dots, commas = body.count("."), body.count(",")
    if not dots and not commas:
        return body, "", False, False
    if body[0] in ".,":  # ".5"
        return ("0", body[1:], False, False) if body.count(body[0]) == 1 and body[1:].isdigit() else None

    if dots and commas:
        dec = "." if body.rindex(".") > body.rindex(",") else ","
        thou = "," if dec == "." else "."
        if body.count(dec) != 1:
            return None
        left, right = body.split(dec)
        groups = left.split(thou)
        if not _groups_ok(groups, thou) or not right.isdigit() or body.index(dec) < body.rfind(thou):
            return None
        return "".join(groups), right, False, True

    sep = "." if dots else ","
    n = body.count(sep)
    if n > 1:
        groups = body.split(sep)
        if not _groups_ok(groups, sep):
            return None
        return "".join(groups), "", False, True
    left, right = body.split(sep)
    if not left.isdigit() or not right.isdigit():
        return None
    looks_grouped = len(right) == 3 and 1 <= len(left) <= 3
    thousands = (sep == "," and locale != "eu" and looks_grouped) or (sep == "." and locale == "eu" and looks_grouped)
    if thousands:
        return left + right, "", locale == "auto", True
    return left, right, locale == "auto" and sep == "." and looks_grouped, False


def parse_number(text: str, *, locale: Literal["auto", "en", "eu"] = "auto") -> ParsedNumber | None:
    """Parse a whole string as one quantity, or return None. Never guesses at prose."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = _to_ascii_digits(unicodedata.normalize("NFC", text)).translate(_MINUS_LIKE).strip()
    parens = s.startswith("(") and s.endswith(")")
    if parens:
        s = s[1:-1].strip()
    m = _PARSE_RE.match(s)
    if not m:
        return None

    negs = [g for g in (m["s1"], m["s2"], m["s3"]) if g == "-"]
    if len(negs) + parens > 1:
        return None
    cur_pre = m["c1"] or (m["k1"].upper() if m["k1"] else None)
    cur_post = m["k2"].upper() if m["k2"] else None  # upper() leaves currency symbols unchanged
    if cur_pre and cur_post:
        return None
    cur = cur_pre or cur_post
    if cur and m["pct"]:
        return None
    cur = _CODE_TO_SYMBOL.get(cur, cur) if cur else ""

    scale = 0
    if m["scale"]:
        tok = m["scale"]
        key = tok.lower()
        if key in _SCALE_WORDS:
            scale = _SCALE_WORDS[key]
        elif key in _SCALE_ABBR:
            # lowercase m/b/t/mm/mn/bn/tn collide with metres, bytes, tonnes, millimetres: need a currency
            if key != "k" and tok == key and not cur:
                return None
            scale = _SCALE_ABBR[key]
        else:
            return None

    interp = _interpret(m["body"], locale)
    if interp is None:
        return None
    int_part, frac, ambiguous, grouped = interp
    if not int_part.isdigit() or (frac and not frac.isdigit()):
        return None
    try:
        value = Decimal(f"{int_part}.{frac}" if frac else int_part)
    except InvalidOperation:
        return None
    if scale:
        value = value.scaleb(scale)
    negative = bool(negs) or parens
    style = "parens" if parens else "trailing" if m["s3"] else "minus" if negs else ""
    return ParsedNumber(
        value=-value if negative and value != 0 else value,
        unit="%" if m["pct"] else cur,
        decimals=len(frac),
        scale=scale,
        negative_style=style,
        identifier_like=not grouped and len(int_part) > 1 and int_part.startswith("0"),
        ambiguous=ambiguous,
        digit_string=int_part + frac,
        raw=text,
    )


def numbers_equivalent(
    a: str,
    b: str,
    *,
    locale: Literal["auto", "en", "eu"] = "auto",
    precision: Literal["digits", "value"] = "digits",
) -> bool:
    """True only if both strings are numbers that denote the same quantity.

    Currency and percent must match ("$5" != "5" != "5%"). Identifier-like numbers ("007")
    must match digit-for-digit. precision="digits" (default) also requires the same number of
    written decimals, so "1.50" != "1.5"; use "value" to accept that.
    """
    pa, pb = parse_number(a, locale=locale), parse_number(b, locale=locale)
    if pa is None or pb is None:
        return False
    if pa.unit != pb.unit or pa.value != pb.value:
        return False
    if (pa.identifier_like or pb.identifier_like) and pa.digit_string != pb.digit_string:
        return False
    if precision == "digits":
        return pa.canonical(True) == pb.canonical(True)
    return True


# In running text only thin / non-breaking spaces and apostrophes may group digits; a plain space
# is a word break ("3.14.15, 1,50" is two numbers). Whole-cell parsing still accepts plain spaces.
_TEXT_BODY = r"(?:\d{1,3}(?:['\u00a0\u2009\u202f]\d{3})+(?:[.,]\d+)?|\d[\d.,']*\d|\d|[.,]\d+)"
_TEXT_NUM = re.compile(
    rf"""(?<![\w.,'])
    (?:[+\-\u2212]\s?)?
    (?:(?:[{re.escape(_CUR_SYMBOLS)}]\s?)|(?:(?:{_CODES_RE})\s))?
    (?:[+\-\u2212]\s?)?
    {_TEXT_BODY}
    (?:\s?(?:thousand|million|billion|trillion|[A-Za-z]{{1,2}}))?
    (?:\s?(?:%|percent|pct))?
    (?:\s(?:{_CODES_RE})|\s?[{re.escape(_CUR_SYMBOLS)}])?
    (?![\w])""",
    re.X | re.I,
)


def canonicalize_numbers(text: str, *, locale: Literal["auto", "en", "eu"] = "auto", keep_precision: bool = True) -> str:
    """Rewrite each number inside running text to one canonical spelling.

    '$18.2 million', '$18.2M' and '$18,200,000' all become '$18200000'; '$13.2M' stays
    '$13200000'. Tokens that are not clearly a single number, and identifier-like tokens such
    as '007', are left untouched. Parentheses in prose are never read as negatives.
    """

    def repl(m: re.Match) -> str:
        span = m.group(0)
        words = span.split(" ")
        for end in range(len(words), 0, -1):  # shed trailing words that were not part of the number
            cand = " ".join(words[:end])
            p = parse_number(cand, locale=locale)
            if p is not None and not p.identifier_like:
                return p.canonical(keep_precision) + span[len(cand):]
            if p is not None:
                return span
        return span

    return _TEXT_NUM.sub(repl, text)
