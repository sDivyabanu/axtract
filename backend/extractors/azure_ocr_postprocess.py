"""Deterministic helpers around the Azure page transcription: scan regions, furniture labels, table and unit repair.

Everything here is generic page/markup handling. Nothing is keyed to a document, dataset or expected answer.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np
from PIL import Image, ImageOps

# ── scanned page regions ─────────────────────────────────────────────────────


def _ink_profile(gray: np.ndarray) -> np.ndarray:
    """Fraction of dark pixels per column over the central band of rows (ignores scanner edges)."""
    h = gray.shape[0]
    band = gray[int(h * .08):int(h * .92)]
    threshold = min(160, float(np.percentile(band, 50)) - 40)
    profile = (band < threshold).mean(axis=0)
    # A median filter removes thin dark verticals (fold lines, page edges) that are not text.
    k = max(3, (len(profile) // 80) | 1)
    padded = np.pad(profile, k // 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, k)
    return np.median(windows, axis=1)


def spread_split(image: Image.Image) -> list[tuple[int, int, int, int]] | None:
    """Boxes for the two pages of a scanned two-page spread, or None.

    A spread is a landscape image whose central region holds a blank vertical gutter with substantial text on both
    sides. Single pages, portrait pages and landscape tables/forms without a central gutter are left whole.
    """
    w, h = image.size
    if w < h * 1.15 or w < 800:
        return None
    small = image.convert("L")
    scale = 600 / w
    small = small.resize((600, max(1, int(h * scale))), Image.Resampling.BILINEAR)
    profile = _ink_profile(np.asarray(small, dtype=np.float32))
    n = len(profile)
    lo, hi = int(n * .38), int(n * .62)
    centre = profile[lo:hi]
    gutter = lo + int(np.argmin(centre))
    left, right = profile[int(n * .05):gutter - 6], profile[gutter + 6:int(n * .95)]
    if len(left) < 20 or len(right) < 20:
        return None
    left_level, right_level = float(np.percentile(left, 70)), float(np.percentile(right, 70))
    if min(left_level, right_level) < .008:
        return None
    # Text on both sides, each judged against its own density (one page may be faint typescript, the other ink).
    left_ink, right_ink = float(np.mean(left > left_level * .35)), float(np.mean(right > right_level * .35))
    floor = min(left_level, right_level) * .12
    if profile[gutter] > floor or left_ink < .35 or right_ink < .35:
        return None
    # The gutter must be a band over the whole height, not a single blank column between two words.
    blank = profile <= floor
    a = b = gutter
    while a > 0 and blank[a - 1]:
        a -= 1
    while b < n - 1 and blank[b + 1]:
        b += 1
    if (b - a + 1) < n * .006:
        return None
    cut = int((a + b + 1) / 2 / scale)
    pad = int(w * .01)
    return [(0, 0, min(w, cut + pad), h), (max(0, cut - pad), 0, w, h)]


def enhance_scan(image: Image.Image) -> Image.Image:
    """Grayscale with a mild contrast stretch: faint ink and yellowed paper become easier to read."""
    gray = ImageOps.autocontrast(image.convert("L"), cutoff=(1, 0.5))
    return gray.convert("RGB")


# ── furniture labels ─────────────────────────────────────────────────────────

_FURNITURE = re.compile(r"<(page-header|page-footer)>\s*(.*?)\s*</\1>", re.S | re.I)
_STRAY_TAG = re.compile(r"</?(?:page-header|page-footer)>", re.I)


def split_furniture(text: str) -> tuple[str, list[str], list[str]]:
    """Separate model-labelled running headers/footers from the body text."""
    headers, footers = [], []

    def take(match):
        (headers if match.group(1).lower() == "page-header" else footers).append(match.group(2).strip())
        return "\n"

    body = _FURNITURE.sub(take, text)
    body = _STRAY_TAG.sub("", body)  # an unclosed label must never hide its text
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, [h for h in headers if h], [f for f in footers if f]


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def native_coverage(text: str, lines: list[tuple[float, str]], min_words: int = 4) -> tuple[float, int, list[str]]:
    """How much of the visible text layer the transcription contains: (coverage, words checked, missing lines).

    Only text-layer lines with at least `min_words` words of 3+ letters are checked, so chart tick labels, isolated
    numbers and short labels never count as omissions. A line is missing when most of its words are absent.
    """
    have = {w for w in _words(unicodedata.normalize("NFKC", text)) if len(w) >= 3}
    total = lost = 0
    missing = []
    for _, line in lines:
        words = [w for w in _words(unicodedata.normalize("NFKC", line)) if len(w) >= 3 and not w.isdigit()]
        if len(words) < min_words:
            continue
        absent = sum(w not in have for w in words)
        total += len(words)
        lost += absent
        if absent / len(words) > .5:
            missing.append(line)
    return (1 - lost / total if total else 1.0), total, missing


def furniture_in_margin(item: str, lines: list[tuple[float, str]], top: float = .16, bottom: float = .84) -> bool:
    """True unless the page's own text layer places this passage in the body region.

    `lines` are (vertical centre as a fraction of page height, text) for the visible native text. With no usable native
    evidence the model's label stands. A passage found only in the middle of the page is body text, whatever the label.
    """
    words = _words(item)
    if not words or not lines:
        return True
    wanted = set(words)
    best, where = 0.0, None
    for y, text in lines:
        have = set(_words(text))
        if not have:
            continue
        score = len(wanted & have) / len(wanted | have) if len(wanted) > 2 else float(wanted <= have)
        if score > best:
            best, where = score, y
    if best < .5 or where is None:
        return True
    return where < top or where > bottom


_PROTECTED = re.compile(r"\$\$.*?\$\$|(?<![\\$])\$(?!\$).*?(?<![\\$])\$(?!\$)|<[^>]*>|^\s*\|.*$|`[^`]*`|https?://\S+",
                        re.S | re.M)
_WORD = re.compile(r"[^\W\d_]+")


def native_spelling(text: str, lines: list[tuple[float, str]], min_ratio: float = .75) -> tuple[str, int]:
    """Correct near-miss words in prose against the page's own visible text layer. Returns (text, corrections).

    The transcription and the text layer are aligned as word sequences. Inside an aligned stretch where both sides
    have the same number of differing words (at most 3), a transcribed word is replaced by the source word when the
    two are close spellings of each other (a misread letter, a dropped accent). Math, tables, HTML, code and URLs are
    never touched, and nothing is inserted or deleted, so reading order and structure stay the model's.
    """
    import difflib
    if not lines:
        return text, 0
    source = [w for _, line in lines for w in _WORD.findall(unicodedata.normalize("NFKC", line))]
    if len(source) < 20:
        return text, 0
    # Prose stretches of the transcription and their word positions.
    spans, last = [], 0
    for match in _PROTECTED.finditer(text):
        spans.append((last, match.start()))
        last = match.end()
    spans.append((last, len(text)))
    words = [(m.start() + a, m.end() + a, m.group()) for a, b in spans for m in _WORD.finditer(text[a:b])]
    if not words:
        return text, 0
    matcher = difflib.SequenceMatcher(None, [w[2].casefold() for w in words], [s.casefold() for s in source], autojunk=False)
    edits = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or i2 - i1 != j2 - j1 or i2 - i1 > 3:
            continue
        for (start, end, have), want in zip(words[i1:i2], source[j1:j2]):
            if (have != want and min(len(have), len(want)) >= 4
                    and difflib.SequenceMatcher(None, have.casefold(), want.casefold()).ratio() >= min_ratio):
                edits.append((start, end, want))
    for start, end, want in reversed(edits):
        text = text[:start] + want + text[end:]
    return text, len(edits)


_FOLIO = re.compile(r"(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:of|/)\s*\d{1,4})?\.?", re.I)


def margin_furniture(text: str, lines: list[tuple[float, str]], band: float = .06) -> tuple[str, list[str], list[str]]:
    """Unlabelled page furniture at the very start or end of the transcription.

    Removed only with evidence: a lone folio ("12", "Page 3 of 9", "xiv"), or a short line that the page's own text
    layer places in the outer `band` of the page height. Scanning stops at the first ordinary paragraph from each end.
    """
    paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]

    def furniture(paragraph: str, top: bool) -> bool:
        p = paragraph.strip()
        if "\n" in p or p.startswith(("#", "|", "$", "<")) or len(p.split()) > 12:
            return False
        if _FOLIO.fullmatch(p):
            return True
        words = set(_words(p))
        if len(words) < 2:
            return False
        for y, line in lines:
            have = set(_words(line))
            if have and len(words & have) / len(words | have) >= .8 and (y < band if top else y > 1 - band):
                return True
        return False

    headers, footers = [], []
    while len(paragraphs) > 1 and len(headers) < 3 and furniture(paragraphs[0], True):
        headers.append(paragraphs.pop(0).strip())
    while len(paragraphs) > 1 and len(footers) < 3 and furniture(paragraphs[-1], False):
        footers.insert(0, paragraphs.pop().strip())
    return "\n\n".join(paragraphs), headers, footers


# ── tables ───────────────────────────────────────────────────────────────────


def _pipe_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.strip().replace("|", "\\|") for c in cells) + " |"


def repair_tables(text: str) -> str:
    """Turn tab-separated grids into Markdown tables and give pipe tables their separator row.

    The model sometimes writes a real grid of values (often a small table inside a figure) with tab separators,
    which no Markdown reader recognises as a table. Only runs of two or more lines with the same number (>= 2) of
    tab-separated cells are converted. Math blocks are left alone.
    """
    lines = text.split("\n")
    out, i, in_math = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.strip() == "$$" or (line.strip().startswith("$$") and line.strip().count("$$") == 1):
            in_math = not in_math
        if not in_math and "\t" in line and not line.lstrip().startswith("|"):
            j, width = i, len(line.strip().split("\t"))
            while j < len(lines) and "\t" in lines[j] and len(lines[j].strip().split("\t")) == width:
                j += 1
            if width >= 2 and j - i >= 2:
                rows = [lines[k].strip().split("\t") for k in range(i, j)]
                if out and out[-1].strip():
                    out.append("")
                out.append(_pipe_row(rows[0]))
                out.append("| " + " | ".join("---" for _ in rows[0]) + " |")
                out.extend(_pipe_row(r) for r in rows[1:])
                if j < len(lines) and lines[j].strip():
                    out.append("")
                i = j
                continue
        out.append(line)
        i += 1
    return _separate_tables("\n".join(out))


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _separate_tables(text: str) -> str:
    """Blank line around each pipe table so prose never merges into its first or last row."""
    lines = text.split("\n")
    out = []
    for k, line in enumerate(lines):
        if _is_table_line(line) and out and out[-1].strip() and not _is_table_line(out[-1]):
            out.append("")
        out.append(line)
        if _is_table_line(line) and k + 1 < len(lines) and lines[k + 1].strip() and not _is_table_line(lines[k + 1]):
            out.append("")
    return "\n".join(out)


# ── HTML in the transcription ────────────────────────────────────────────────

_HTML_TABLE = re.compile(r"<table\b.*?</table\s*>", re.S | re.I)


def split_html_tables(text: str) -> list[tuple[str, str]]:
    """[("text", ...), ("table", "<table>...</table>"), ...] in reading order."""
    parts, last = [], 0
    for match in _HTML_TABLE.finditer(text):
        if text[last:match.start()].strip():
            parts.append(("text", text[last:match.start()].strip()))
        parts.append(("table", match.group(0)))
        last = match.end()
    if text[last:].strip():
        parts.append(("text", text[last:].strip()))
    return parts


_SUB = str.maketrans("0123456789+-−=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎")


def clean_inline_html(text: str) -> str:
    """Inline HTML the model sometimes emits in prose, turned into the plain text it stands for.

    The output sanitiser would otherwise encode it as literal `&lt;sup>` text. Digits in <sup>/<sub> become Unicode
    super/subscripts (m², H₂O); other contents are kept as plain text. Emphasis/span tags are dropped, text kept.
    """
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)

    def script(match, table):
        inner = match.group(2)
        return inner.translate(table) if re.fullmatch(r"[0-9+\-−=()]+", inner) else inner

    text = re.sub(r"<(sup)>(.*?)</sup>", lambda m: script(m, _SUP), text, flags=re.I | re.S)
    text = re.sub(r"<(sub)>(.*?)</sub>", lambda m: script(m, _SUB), text, flags=re.I | re.S)
    return re.sub(r"</?(?:b|i|u|em|strong|span|small|font|mark|ins|del|s)(?:\s[^<>]*)?>", "", text, flags=re.I)


# ── prose superscripts ───────────────────────────────────────────────────────

_SUP = str.maketrans("0123456789+-−=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿ")
_BARE_SUP = re.compile(r"(?<![\\$])\$\s*\^\s*(?:\{\s*([0-9+\-−=()n]{1,4})\s*\}|([0-9]))\s*\$(?!\$)")
_DEGREE = re.compile(r"(?<![\\$])\$\s*\^\s*\{?\s*\\circ\s*\}?\s*\$(?!\$)")


def prose_superscripts(text: str) -> str:
    """`kg/m$^2$` -> `kg/m²`, `25$^\\circ$C` -> `25°C`.

    Only a math span that consists of nothing but a bare superscript is rewritten; any span with a base, operator or
    command (a real expression) is kept as LaTeX.
    """
    text = _DEGREE.sub("°", text)
    return _BARE_SUP.sub(lambda m: (m.group(1) or m.group(2)).translate(_SUP), text)
