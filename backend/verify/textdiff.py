"""Token-level text comparison for the content layer.

Two ways to compare, chosen by how reliable the ORDER of the two texts is:
  diff_sequence   order-aware (difflib). For Office objects, where source and output are the same
                  paragraph or shape and order is exact.
  diff_multiset   order-insensitive. For PDF pages, where reading order is an interpretation and
                  only "which words and numbers are present" is trustworthy.

Both return the same thing: a list of changes, each one of
  changed   source tokens that appear as different tokens in the output (a digit changed, a word
            substituted, an OCR-style error)
  missing   source tokens with no counterpart in the output
  extra     output tokens with no counterpart in the source

A change is "numeric" when any token involved contains a digit; numbers are the content most
costly to get wrong, so they are reported separately and at higher severity.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from Levenshtein import ratio as _ratio

from verify.normalize import normalize_text

_CONTENT_TOKEN = re.compile(r"[$€£¥₹₩₽₺₫฿₪₱]?-?\d+(?:\.\d+)?%?|\w+", re.UNICODE)
_PAIR_LIMIT = 40_000  # cap on missing x extra pairings considered for "changed"


def content_tokens(text: str | None) -> list[str]:
    """Case-insensitive tokens in which a number is ONE token ("18.2", "$18200000", "12.5%")."""
    if not text:
        return []
    return _CONTENT_TOKEN.findall(normalize_text(text, case_sensitive=False, canonical_numbers=True))


def has_digit(token: str) -> bool:
    return any(c.isdigit() for c in token)


@dataclass(frozen=True)
class Change:
    kind: str  # changed | missing | extra
    src: tuple[str, ...] = ()
    out: tuple[str, ...] = ()

    @property
    def numeric(self) -> bool:
        return any(has_digit(t) for t in self.src + self.out)

    def describe(self) -> str:
        s, o = " ".join(self.src), " ".join(self.out)
        return {"changed": f"{s!r} -> {o!r}", "missing": f"{s!r} missing", "extra": f"{o!r} added"}[self.kind]


@dataclass
class TextDiff:
    changes: list[Change] = field(default_factory=list)
    src_tokens: int = 0
    out_tokens: int = 0

    @property
    def equal(self) -> bool:
        return not self.changes

    def of(self, kind: str, numeric: bool | None = None) -> list[Change]:
        return [c for c in self.changes if c.kind == kind and (numeric is None or c.numeric == numeric)]

    @property
    def lost_tokens(self) -> int:
        return sum(len(c.src) for c in self.changes if c.kind in ("changed", "missing"))


def diff_sequence(src: list[str], out: list[str]) -> TextDiff:
    d = TextDiff(src_tokens=len(src), out_tokens=len(out))
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=src, b=out, autojunk=False).get_opcodes():
        if tag == "replace":
            d.changes.append(Change("changed", tuple(src[i1:i2]), tuple(out[j1:j2])))
        elif tag == "delete":
            d.changes.append(Change("missing", tuple(src[i1:i2])))
        elif tag == "insert":
            d.changes.append(Change("extra", (), tuple(out[j1:j2])))
    return d


def diff_multiset(src: list[str], out: list[str], ignore_extra: Counter | None = None,
                  ignore_missing: Counter | None = None) -> TextDiff:
    """Order-insensitive comparison.

    `ignore_extra`   tokens that may appear in the output without being reported (OCR text)
    `ignore_missing` tokens that may be absent from the output without being reported (header rows that
                     AXTRACT removes on purpose when a table continues onto another page)
    """
    cs, co = Counter(src), Counter(out)
    gone = cs - co
    if ignore_missing:
        gone = gone - ignore_missing
    missing = sorted(gone.elements())
    extra_c = co - cs
    if ignore_extra:
        extra_c = extra_c - ignore_extra
    extra = sorted(extra_c.elements())
    d = TextDiff(src_tokens=len(src), out_tokens=len(out))
    if len(missing) * len(extra) <= _PAIR_LIMIT:
        scored = []
        for i, m in enumerate(missing):
            for j, e in enumerate(extra):
                both_num = has_digit(m) and has_digit(e)
                r = _ratio(m, e)
                if r >= (0.5 if both_num else 0.6):
                    scored.append((-r, i, j))
        used_m, used_e = set(), set()
        for _, i, j in sorted(scored):
            if i in used_m or j in used_e:
                continue
            used_m.add(i)
            used_e.add(j)
            d.changes.append(Change("changed", (missing[i],), (extra[j],)))
        missing = [m for i, m in enumerate(missing) if i not in used_m]
        extra = [e for j, e in enumerate(extra) if j not in used_e]
    d.changes += [Change("missing", (m,)) for m in missing]
    d.changes += [Change("extra", (), (e,)) for e in extra]
    return d
