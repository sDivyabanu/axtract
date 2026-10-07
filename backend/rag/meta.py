"""Lightweight metadata detection: units, currency, periods, document type, printed page numbers."""

from __future__ import annotations

import re
from collections import Counter

# (pattern, scale multiplier, label)
_UNITS: list[tuple[re.Pattern, float, str]] = [
    (re.compile(r"\bin\s+(?:rs\.?|₹|inr)?\s*(?:crores?|cr\.?)\b|\b(?:₹|rs\.?|inr)\s*(?:in\s+)?crores?\b|\bcrores?\b", re.I), 1e7, "crore"),
    (re.compile(r"\blakhs?\b|\blacs?\b", re.I), 1e5, "lakh"),
    (re.compile(r"\bin\s+(?:us\$|\$|usd|eur|€|gbp|£)?\s*billions?\b|\b(?:us\$|\$)\s*(?:in\s+)?billions?\b|\bbillions?\b|\bbn\b", re.I), 1e9, "billion"),
    (re.compile(r"\bin\s+(?:us\$|\$|usd|eur|€|gbp|£)?\s*millions?\b|\b(?:us\$|\$|usd)\s*(?:in\s+)?millions?\b|\bmillions?\b|\bmn\b|\$\s*m\b", re.I), 1e6, "million"),
    (re.compile(r"\bin\s+(?:us\$|\$|usd|rs\.?|₹)?\s*(?:thousands?|'000s?|000s)\b|\bthousands?\b|\(?\$?\s*'000\)?", re.I), 1e3, "thousand"),
]
_CURRENCIES = [
    (re.compile(r"₹|\brs\.?\s|\binr\b|\brupees?\b", re.I), "INR"),
    (re.compile(r"\$|\busd\b|\bus dollars?\b", re.I), "USD"),
    (re.compile(r"€|\beur\b|\beuros?\b", re.I), "EUR"),
    (re.compile(r"£|\bgbp\b", re.I), "GBP"),
    (re.compile(r"¥|\bjpy\b|\byen\b", re.I), "JPY"),
]


def detect_unit(text: str) -> tuple[str | None, float | None, str | None]:
    """(unit label, scale multiplier, currency) found in a caption/header text."""
    scale = label = None
    for pat, mult, name in _UNITS:
        if pat.search(text):
            scale, label = mult, name
            break
    currency = next((c for pat, c in _CURRENCIES if pat.search(text)), None)
    return label, scale, currency


# "Amounts are in ₹ crore", "All figures in USD millions", "(₹ in crore)" - an explicit unit statement,
# as opposed to a passing mention of "million" in prose.
_UNIT_STATEMENT = re.compile(
    r"(?:amounts?|figures?|numbers?|values?|currency|unless otherwise stated|all\s+(?:amounts|figures)).{0,40}\bin\b"
    r"|\(\s*(?:₹|rs\.?|\$|us\$|usd|eur|€|£|inr)?\s*in\s+[a-z']+\s*\)"
    r"|\bin\s+(?:₹|rs\.?|us\$|\$|usd|eur|€|£|inr)?\s*(?:crores?|lakhs?|millions?|billions?|thousands?)\b", re.I)


def unit_statement(text: str) -> tuple[str | None, float | None, str | None]:
    """Unit from a sentence that explicitly states the unit of the amounts; else (None, None, None)."""
    head = text[:260]
    if _UNIT_STATEMENT.search(head):
        return detect_unit(head)
    return (None, None, None)


_FY = re.compile(r"\bFY\s*'?(\d{4}|\d{2})(?:\s*[-/–]\s*'?(\d{2,4}))?\b", re.I)
_RANGE = re.compile(r"\b(20\d{2})\s*[-–/]\s*(\d{2}|20\d{2})\b")
_QTR = re.compile(r"\bQ([1-4])\s*(?:FY)?\s*'?(\d{2,4})\b", re.I)
_YEAR_END = re.compile(
    r"(?:year|period|quarter)s?\s+ended\s+(?:on\s+)?(?:\d{1,2}(?:st|nd|rd|th)?\s+)?"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s*(?:\d{1,2},?\s*)?(20\d{2})",
    re.I,
)
_YEAR = re.compile(r"\b(20[0-4]\d)\b")


def _full_year(y: str) -> int:
    n = int(y)
    return n if n >= 100 else 2000 + n


def normalize_period(text: str) -> str | None:
    """Canonical period like FY2025 / Q2FY2025, from FY25, 2024-25, 'year ended March 31, 2025'."""
    m = _QTR.search(text)
    if m:
        return f"Q{m.group(1)}FY{_full_year(m.group(2))}"
    m = _FY.search(text)
    if m:
        a, b = m.group(1), m.group(2)
        if b:  # FY2024-25 -> FY2025
            end = _full_year(b) if len(b) == 4 else (_full_year(a) // 100) * 100 + int(b)
            return f"FY{end}"
        return f"FY{_full_year(a)}"
    m = _RANGE.search(text)
    if m:
        a, b = m.groups()
        end = int(b) if len(b) == 4 else (int(a) // 100) * 100 + int(b)
        return f"FY{end}"
    m = _YEAR_END.search(text)
    if m:
        return f"FY{m.group(1)}"
    m = _YEAR.search(text)
    return f"FY{m.group(1)}" if m else None


def periods_in(text: str) -> list[str]:
    seen: list[str] = []
    for pat in (_QTR, _FY, _RANGE, _YEAR_END, _YEAR):
        for m in pat.finditer(text):
            p = normalize_period(m.group(0))
            if p and p not in seen:
                seen.append(p)
    return seen


_DOC_TYPE_HINTS: dict[str, list[str]] = {
    "financial_statement": ["balance sheet", "statement of profit", "profit and loss", "cash flow", "audited", "financial statements",
                            "independent auditor", "total assets", "shareholders' equity", "income statement"],
    "cim": ["confidential information memorandum", "investment highlights", "executive summary", "management presentation",
            "company overview", "transaction overview"],
    "contract": ["agreement", "hereinafter", "whereas", "governing law", "termination", "indemnif", "change of control",
                 "representations and warranties", "this deed"],
    "debt_schedule": ["debt schedule", "maturity", "term loan", "revolver", "facility", "tranche", "borrowings", "repayment schedule",
                      "covenant"],
    "bank_statement": ["bank statement", "opening balance", "closing balance", "withdrawal", "deposit", "account number"],
}


def guess_doc_type(filename: str, text_sample: str, file_type: str) -> str:
    hay = (filename + " " + text_sample[:6000]).lower()
    scores = Counter()
    for kind, words in _DOC_TYPE_HINTS.items():
        scores[kind] = sum(hay.count(w) for w in words)
    if "cim" in filename.lower().split("."):
        scores["cim"] += 3
    kind, score = scores.most_common(1)[0]
    if score >= 2:
        return kind
    if file_type == "pptx":
        return "presentation"
    if file_type == "xlsx":
        return "spreadsheet"
    return "other"


_STATEMENTS = [
    ("balance_sheet", re.compile(r"balance sheet|statement of financial position|total assets", re.I)),
    ("profit_loss", re.compile(r"profit and loss|profit or loss|statement of (?:profit|income|operations)|income statement|revenue from operations", re.I)),
    ("cash_flow", re.compile(r"cash flows?", re.I)),
    ("debt_schedule", re.compile(r"debt schedule|maturity|borrowings|term loan|repayment", re.I)),
]


def detect_statement(text: str) -> str | None:
    for name, pat in _STATEMENTS:
        if pat.search(text):
            return name
    return None


_PRINTED = re.compile(r"^(?:page\s+)?([A-Za-z]{1,2}\s?[-–]\s?\d{1,4}|\d{1,4})(?:\s+of\s+\d+)?$", re.I)


def printed_page(text: str) -> str | None:
    """The page number printed in a header/footer ('F-3', '12', 'Page 4 of 9')."""
    t = " ".join(text.split())
    if len(t) > 14:
        return None
    m = _PRINTED.match(t)
    return m.group(1).replace(" ", "") if m else None
