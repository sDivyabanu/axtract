"""Grounding verifier: every number, date and entity in an answer must be supported.

A claim is supported when it appears in a chunk the sentence cites, or is the output of a
computed receipt. Sentences with unsupported claims (or no citation at all) are marked
unverified; the answer gets a grounding score "k/n claims verified".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM = re.compile(r"(?<![\w.])\(?[-+−]?[$₹€£]?\s?\d[\d,]*(?:\.\d+)?\)?\s?%?")
_MONTHS = ("january february march april may june july august september october november december "
           "jan feb mar apr jun jul aug sep sept oct nov dec").split()
_WORD = re.compile(r"[A-Za-z][A-Za-z&'\-]+")
# capitalised words that are not entities (sentence furniture, generic finance words)
_GENERIC = frozenset(
    "the a an and or of in on at to for with by from as is are was were be been this that these those it its "
    "according total net gross not found data room document page table section note based following per "
    "fy q1 q2 q3 q4 inr usd eur gbp crore lakh million billion thousand".split()
)


@dataclass
class Claim:
    kind: str  # number | entity
    text: str
    supported: bool


@dataclass
class SentenceCheck:
    text: str
    citations: list[int]
    claims: list[Claim] = field(default_factory=list)
    verified: bool = False
    reason: str = ""


def _to_float(token: str) -> float | None:
    t = token.strip().replace("−", "-")
    neg = t.startswith("(") and t.endswith(")") or t.startswith("-")
    t = re.sub(r"[()$₹€£%\s+\-,]", "", t)
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUM.finditer(text):
        v = _to_float(m.group(0))
        if v is not None:
            out.append(abs(v))
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, 1e-6 * max(abs(a), abs(b)))


def _entities(sentence: str) -> list[str]:
    """Capitalised, non-generic words not at the start of the sentence (proper nouns)."""
    words = _WORD.findall(sentence)
    out = []
    for i, w in enumerate(words):
        if i == 0 or not w[0].isupper() or len(w) < 4 or any(ch.isdigit() for ch in w):
            continue
        if w.lower() in _GENERIC or w.lower() in _MONTHS:
            continue
        out.append(w)
    return out


def check_sentence(
    sentence: str,
    citations: list[int],
    sources: dict[int, str],
    receipt_numbers: list[float] | None = None,
) -> SentenceCheck:
    """Verify one sentence against the sources it cites (and any computed receipt values)."""
    chk = SentenceCheck(sentence, citations)
    if not citations and not receipt_numbers:
        chk.reason = "no citation"
        return chk

    pool_text = " ".join(sources.get(n, "") for n in citations)
    pool_low = pool_text.lower()
    pool_nums = numbers_in(pool_text) + list(receipt_numbers or [])

    for m in _NUM.finditer(sentence):
        v = _to_float(m.group(0))
        if v is None:
            continue
        v = abs(v)
        chk.claims.append(Claim("number", m.group(0).strip(), any(_close(v, p) for p in pool_nums)))
    for ent in _entities(sentence):
        chk.claims.append(Claim("entity", ent, ent.lower() in pool_low))

    if not chk.claims:  # nothing checkable: a cited statement counts as one supported claim
        chk.claims.append(Claim("statement", sentence[:60], bool(citations)))
    bad = [c for c in chk.claims if not c.supported]
    chk.verified = not bad
    if bad:
        chk.reason = "not found in cited source: " + ", ".join(c.text for c in bad[:4])
    return chk


def grounding(checks: list[SentenceCheck]) -> dict[str, int]:
    total = sum(len(c.claims) or 1 for c in checks)
    ok = sum(sum(1 for cl in c.claims if cl.supported) for c in checks)
    return {"verified": ok, "total": total}
