"""Rebuild mathematics from a digital PDF's text layer as LaTeX.

A TeX-produced PDF already holds every glyph of every formula, with its font, size and position.
Reading it as plain words flattens the structure: `x_i^*` becomes "x∗ i", a fraction becomes two
lines, a summation's limits become stray fragments. This module puts the structure back from
geometry alone, with no model and nothing guessed:

  * scripts     a smaller glyph raised or lowered against its row's baseline
  * fractions   glyphs directly above and below a short horizontal rule
  * radicals    glyphs under the rule that starts at a radical sign
  * limits      small glyphs directly above / below a large operator
  * accents     a bar, hat, tilde or dot glyph sitting over the next glyph

Math is recognised by font (CMMI, CMSY, MSBM, ...): prose words stay prose, and only runs that
contain at least one glyph from a math font become `$...$`. A row that is entirely math becomes a
display equation `$$...$$`. Pages without math fonts are never touched.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

MATH_FONT = re.compile(
    r"cmmi|cmsy|cmex|cmbsy|msbm|msam|eufm|eufb|eusm|eusb|eurm|eurb|rsfs|lmmath|latinmodernmath"
    r"|tex-math|mnsymbol|stix\w*math|xits\w*math|cambria\s*math|cambriamath|txmi|txsy|txex|ntxmi"
    r"|ntxsy|ntxex|pxmi|pxsy|pxex|zplm|mathematicalpi|euclid|esint|wasy|bbold|dsrom|bbm|stmary"
    r"|pazomath|mathdesign|fourier-?m|^symbol(mt)?$|^cmbx\d+$(?!x)",
    re.I,
)
_BOLD_TEX_FONT = re.compile(r"^(cmbx|lmroman\d*-bold|cmmib|cmbsy)", re.I)
_EXTENSION_FONT = re.compile(r"cmex|lmmathextension|txex|pxex|tex-mathx", re.I)
_CALLIGRAPHIC_FONT = re.compile(r"cmsy|lmmathsymbols|cmbsy|txsy|pxsy|tex-maths|tex-matha", re.I)
_CMSY_FONT = re.compile(r"cmsy|lmmathsymbols|cmbsy|txsy|pxsy|tex-maths|tex-matha", re.I)
_BLACKBOARD_FONT = re.compile(r"msbm|bbold|dsrom|bbm", re.I)
_FRAKTUR_FONT = re.compile(r"eufm|eufb", re.I)
_SCRIPT_FONT = re.compile(r"rsfs|eusm|eusb", re.I)

# Glyph -> LaTeX. Characters not listed (ASCII letters, digits, most punctuation) stand for
# themselves.
_SYMBOLS = {
    "<": r"\lt", ">": r"\gt",  # preserve relations through HTML-safe text sanitisation
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ϵ": r"\epsilon", "ε": r"\varepsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "µ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi",
    "ρ": r"\rho", "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon",
    "φ": r"\varphi", "ϕ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "∆": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi",
    "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega", "Ω": r"\Omega",
    "∑": r"\sum", "∏": r"\prod", "∐": r"\coprod", "∫": r"\int", "∬": r"\iint", "∭": r"\iiint",
    "∮": r"\oint", "⋃": r"\bigcup", "⋂": r"\bigcap", "⨁": r"\bigoplus", "⨂": r"\bigotimes",
    "⋁": r"\bigvee", "⋀": r"\bigwedge",
    "≤": r"\leq", "≥": r"\geq", "⩽": r"\leq", "⩾": r"\geq", "≠": r"\neq", "≈": r"\approx",
    "≡": r"\equiv", "∼": r"\sim", "≃": r"\simeq", "≅": r"\cong", "∝": r"\propto", "≪": r"\ll",
    "≫": r"\gg", "≺": r"\prec", "≻": r"\succ", "⪯": r"\preceq", "⪰": r"\succeq", "≼": r"\preceq",
    "≽": r"\succeq", "⊂": r"\subset", "⊃": r"\supset", "⊆": r"\subseteq", "⊇": r"\supseteq",
    "⊊": r"\subsetneq", "∈": r"\in", "∉": r"\notin", "∋": r"\ni", "∪": r"\cup", "∩": r"\cap",
    "∖": r"\setminus", "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists", "∄": r"\nexists",
    "¬": r"\neg", "∧": r"\wedge", "∨": r"\vee", "→": r"\rightarrow", "←": r"\leftarrow",
    "↔": r"\leftrightarrow", "⇒": r"\Rightarrow", "⇐": r"\Leftarrow", "⇔": r"\Leftrightarrow",
    "↦": r"\mapsto", "⟶": r"\longrightarrow", "⟵": r"\longleftarrow", "⟹": r"\Longrightarrow",
    "↑": r"\uparrow", "↓": r"\downarrow", "⇀": r"\rightharpoonup", "↪": r"\hookrightarrow",
    "∞": r"\infty", "∂": r"\partial", "∇": r"\nabla", "±": r"\pm", "∓": r"\mp", "×": r"\times",
    "÷": r"\div", "·": r"\cdot", "⋅": r"\cdot", "∙": r"\cdot", "∗": "*", "⋆": r"\star", "∘": r"\circ",
    "⊕": r"\oplus", "⊖": r"\ominus", "⊗": r"\otimes", "⊙": r"\odot", "⊘": r"\oslash", "†": r"\dagger",
    "‡": r"\ddagger", "−": "-", "–": "-", "…": r"\ldots", "⋯": r"\cdots", "⋮": r"\vdots", "⋱": r"\ddots",
    "′": "'", "″": "''", "‖": r"\|", "∥": r"\|", "⟨": r"\langle", "⟩": r"\rangle", "〈": r"\langle",
    "〉": r"\rangle", "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil", "⌉": r"\rceil", "ℓ": r"\ell",
    "ℏ": r"\hbar", "ℜ": r"\Re", "ℑ": r"\Im", "ℵ": r"\aleph", "⊥": r"\perp", "⊤": r"\top",
    "∠": r"\angle", "♯": r"\sharp", "♭": r"\flat", "♮": r"\natural", "□": r"\square", "△": r"\triangle",
    "▽": r"\triangledown", "◦": r"\circ", "⊢": r"\vdash", "⊨": r"\models", "∣": r"\mid", "∤": r"\nmid",
    "≲": r"\lesssim", "≳": r"\gtrsim", "≔": ":=", "⪅": r"\lessapprox", "⊲": r"\lhd", "⊳": r"\rhd",
    "⋉": r"\ltimes", "⋊": r"\rtimes", "√": r"\surd", "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}",
    "ℤ": r"\mathbb{Z}", "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "ℙ": r"\mathbb{P}", "𝔼": r"\mathbb{E}",
    "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "&": r"\&", "$": r"\$", "_": r"\_", "~": r"\sim",
    "\\": r"\backslash", "ı": r"\imath", "ȷ": r"\jmath", "℘": r"\wp", "ð": r"\eth", "ﬀ": "ff",
    "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", " ": " ", " ": " ",
    # Variant code points that PDFs commonly produce instead of canonical forms
    "Ω": r"\Omega",  # Ohm Sign (looks identical to U+03A9 but distinct key)
    "∶": ":",  # Ratio -> colon
    "’": "'",  # Right single quotation mark -> prime in math
    "·": r"\cdot",  # Middle dot
    "∗": "*",  # Asterisk operator
    "ϒ": r"\Upsilon",  # Greek Upsilon with hook
}
# CMEX has no Unicode map in many PDFs: PyMuPDF then returns the raw code point.
_CMEX = {
    "P": r"\sum", "X": r"\sum", "Q": r"\prod", "Y": r"\prod", "R": r"\int", "Z": r"\int",
    "H": r"\oint", "I": r"\oint", "S": r"\bigcup", "[": r"\bigcup", "T": r"\bigcap", "\\": r"\bigcap",
    "L": r"\bigoplus", "M": r"\bigoplus", "N": r"\bigotimes", "O": r"\bigotimes", "V": r"\bigwedge",
    "^": r"\bigwedge", "W": r"\bigvee", "_": r"\bigvee", "`": r"\coprod", "a": r"\coprod",
    "q": r"\surd", "p": r"\surd", "r": r"\surd", "s": r"\surd", "t": r"\surd",
}
_CMEX_DELIMS = {
    0: "(", 1: ")", 2: "[", 3: "]", 4: r"\lfloor", 5: r"\rfloor", 6: r"\lceil", 7: r"\rceil",
    8: r"\{", 9: r"\}", 10: r"\langle", 11: r"\rangle", 12: "|", 13: r"\|", 14: "/", 15: r"\backslash",
    16: "(", 17: ")", 18: "(", 19: ")", 20: "[", 21: "]", 22: r"\lfloor", 23: r"\rfloor", 24: r"\lceil",
    25: r"\rceil", 26: r"\{", 27: r"\}", 28: r"\langle", 29: r"\rangle", 30: "/", 31: r"\backslash",
    32: "(", 33: ")", 34: "[", 35: "]", 36: r"\lfloor", 37: r"\rfloor", 38: r"\lceil", 39: r"\rceil",
    40: r"\{", 41: r"\}", 42: r"\langle", 43: r"\rangle", 44: "/", 45: r"\backslash", 46: "/",
    47: r"\backslash",
}
# Apple/Adobe Private Use Area mappings for extensible CMEX delimiters
_CMEX_PUA = {
    0xF8EB: "[", 0xF8ED: "[", 0xF8EE: "(", 0xF8EF: "(", 0xF8F0: "(", 0xF8F1: "/",
    0xF8F4: ")", 0xF8F5: "]", 0xF8F6: ")", 0xF8F7: ")", 0xF8F8: ")", 0xF8F9: "]",
    0xF8FA: "]", 0xF8FB: r"\{", 0xF8FC: r"\{", 0xF8FD: r"\}", 0xF8FE: r"\}", 0xF8FF: r"\}",
}
# CMSY encoding: maps raw byte positions to LaTeX when fonts have broken ToUnicode maps.
# Only applied to fonts that match _CMSY_FONT and produce a character that _SYMBOLS doesn't know.
_CMSY_ENC = {
    0x00: "-", 0x01: r"\cdot", 0x02: r"\times", 0x03: "*", 0x04: r"\div",
    0x05: r"\diamond", 0x06: r"\pm", 0x07: r"\mp", 0x08: r"\oplus", 0x09: r"\ominus",
    0x0A: r"\otimes", 0x0B: r"\oslash", 0x0C: r"\odot", 0x0E: r"\circ", 0x0F: r"\bullet",
    0x10: r"\asymp", 0x11: r"\equiv", 0x12: r"\subseteq", 0x13: r"\supseteq",
    0x14: r"\leq", 0x15: r"\geq", 0x16: r"\preceq", 0x17: r"\succeq",
    0x18: r"\sim", 0x19: r"\approx", 0x1A: r"\subset", 0x1B: r"\supset",
    0x1C: r"\ll", 0x1D: r"\gg", 0x1E: r"\prec", 0x1F: r"\succ",
    0x20: r"\leftarrow", 0x21: r"\rightarrow", 0x22: r"\uparrow", 0x23: r"\downarrow",
    0x24: r"\leftrightarrow", 0x27: r"\simeq",
    0x28: r"\Leftarrow", 0x29: r"\Rightarrow", 0x2A: r"\Uparrow", 0x2B: r"\Downarrow",
    0x2C: r"\Leftrightarrow", 0x2F: r"\propto",
    0x30: "'", 0x31: r"\infty", 0x32: r"\in", 0x33: r"\ni",
    0x34: r"\triangle", 0x35: r"\triangledown",
    0x38: r"\forall", 0x39: r"\exists", 0x3A: r"\neg", 0x3B: r"\emptyset",
    0x3C: r"\Re", 0x3D: r"\Im", 0x3E: r"\top", 0x3F: r"\perp", 0x40: r"\aleph",
    # 0x41-0x5A = calligraphic A-Z (handled by _CALLIGRAPHIC_FONT path)
    0x5B: r"\cup", 0x5C: r"\cap", 0x5D: r"\uplus", 0x5E: r"\wedge", 0x5F: r"\vee",
    0x60: r"\vdash", 0x61: r"\dashv", 0x62: r"\lfloor", 0x63: r"\rfloor",
    0x64: r"\lceil", 0x65: r"\rceil", 0x66: r"\{", 0x67: r"\}", 0x68: r"\langle", 0x69: r"\rangle",
    0x6A: "|", 0x6B: r"\|", 0x6E: r"\backslash", 0x6F: r"\wr",
    0x70: r"\surd", 0x71: r"\coprod", 0x72: r"\nabla", 0x73: r"\int",
    0x74: r"\sqcup", 0x75: r"\sqcap", 0x76: r"\sqsubseteq", 0x77: r"\sqsupseteq",
    0x79: r"\dagger", 0x7A: r"\ddagger", 0x7E: r"\heartsuit",
}
_BIG_OPS = {r"\sum", r"\prod", r"\coprod", r"\bigcup", r"\bigcap", r"\bigoplus", r"\bigotimes",
            r"\bigwedge", r"\bigvee", r"\int", r"\oint", r"\iint", r"\iiint"}
_ACCENTS = {"¯": r"\bar", "ˉ": r"\bar", "ˆ": r"\hat", "^": r"\hat", "˜": r"\tilde", "~": r"\tilde",
            "˙": r"\dot", "¨": r"\ddot", "⃗": r"\vec", "´": r"\acute", "`": r"\grave", "˘": r"\breve",
            "ˇ": r"\check", "→": r"\vec"}
_NEGATION = "̸"
_NEGATED = {"=": r"\neq", r"\in": r"\notin", r"\leq": r"\nleq", r"\geq": r"\ngeq", r"\subset": r"\not\subset",
            r"\subseteq": r"\nsubseteq", r"\equiv": r"\not\equiv", r"\sim": r"\nsim", "|": r"\nmid",
            r"\mid": r"\nmid", r"\exists": r"\nexists"}
_FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "lg", "exp", "lim", "max", "min",
              "sup", "inf", "det", "dim", "ker", "deg", "gcd", "arg", "sinh", "cosh", "tanh", "arcsin",
              "arccos", "arctan", "Pr", "hom", "limsup", "liminf", "mod"}
_OPERATOR_CHARS = set("=+-<>()[]|/,.:;!'*0123456789")


@dataclass
class Tok:
    latex: str
    x0: float
    y0: float
    x1: float
    y1: float
    base: float  # baseline y
    size: float
    math: bool  # comes from a math font, or is built structure (fraction, script ...)
    text: str = ""  # original characters (for prose output)
    block: int = -1
    line: int = -1
    font: str = ""
    space: bool = False
    big_op: bool = False
    accent: str = ""
    letters: bool = False  # alphabetic glyph from a prose font
    italic: bool = False
    bold: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    source_blocks: frozenset[int] = field(default_factory=frozenset)

    def __post_init__(self):
        if not self.source_blocks:
            self.source_blocks = frozenset((self.block,))

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class PageMath:
    """Rebuilt content for the text blocks that held math, keyed by PyMuPDF block index."""

    blocks: dict[int, str] = field(default_factory=dict)
    source_blocks: dict[int, list[int]] = field(default_factory=dict)
    inline_count: int = 0
    display_count: int = 0


def _char_latex(c: str, font: str) -> tuple[str, bool]:
    """LaTeX for one glyph, and whether it is a large operator."""
    if _EXTENSION_FONT.search(font):
        if c in _CMEX:
            return _CMEX[c], _CMEX[c] in _BIG_OPS
        if len(c) == 1 and ord(c) in _CMEX_DELIMS:
            return _CMEX_DELIMS[ord(c)], False
        if len(c) == 1 and ord(c) in _CMEX_PUA:
            return _CMEX_PUA[ord(c)], False
    latex = _SYMBOLS.get(c, c)
    if len(c) == 1 and c.isalpha() and c.isascii():
        if _BLACKBOARD_FONT.search(font) and c.isupper():
            latex = rf"\mathbb{{{c}}}"
        elif _FRAKTUR_FONT.search(font):
            latex = rf"\mathfrak{{{c}}}"
        elif _SCRIPT_FONT.search(font) and c.isupper():
            latex = rf"\mathcal{{{c}}}"
        elif _CALLIGRAPHIC_FONT.search(font) and c.isupper():
            latex = rf"\mathcal{{{c}}}"
        elif _BOLD_TEX_FONT.search(font) or re.search(r"bold|-bd|bx\d", font, re.I):
            latex = rf"\mathbf{{{c}}}"
    if latex == c and len(c) == 1 and not c.isascii() and not c.isalpha():
        # unknown symbol: keep the glyph (KaTeX renders most Unicode math symbols)
        latex = c
    return latex, latex in _BIG_OPS


def _join(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not p:
            continue
        if out and (re.search(r"\\[A-Za-z]+$", out) and re.match(r"[A-Za-z]", p)):
            out += " "
        out += p
    return out


def _is_prose_font(font: str) -> bool:
    return not MATH_FONT.search(font)


# ----------------------------------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------------------------------


def _bars(drawings: list[dict], page_width: float) -> list[tuple[float, float, float, float]]:
    """Short horizontal rules: fraction bars and radical vincula (x0, x1, y, thickness)."""
    bars = []
    for d in drawings or []:
        for item in d.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 0.3:
                    x0, x1 = sorted((p1.x, p2.x))
                    bars.append((x0, x1, (p1.y + p2.y) / 2, float(d.get("width") or 0.4)))
            elif kind == "re":
                r = item[1]
                if r.height <= 1.6 and r.width > r.height * 3:
                    bars.append((r.x0, r.x1, (r.y0 + r.y1) / 2, r.height))
    out = []
    for x0, x1, y, t in bars:
        w = x1 - x0
        if 2.0 <= w <= page_width * 0.45:
            out.append((x0, x1, y, t))
    return out


def _overlap_x(a: Tok, x0: float, x1: float) -> float:
    return max(0.0, min(a.x1, x1) - max(a.x0, x0))


# ----------------------------------------------------------------------------------------------
# building LaTeX for a set of tokens on one visual row
# ----------------------------------------------------------------------------------------------


def _row_metrics(toks: list[Tok]) -> tuple[float, float]:
    sizes = [t.size for t in toks if not t.space]
    if not sizes:
        return toks[0].base, toks[0].size
    main = max(sizes)
    core = [t for t in toks if not t.space and t.size >= 0.86 * main and not t.big_op]
    if not core:
        core = [t for t in toks if not t.space]
    return statistics.median(t.base for t in core), main


def _script_kind(t: Tok, base: float, size: float) -> str:
    if t.space:
        return "base"
    small = t.size < 0.86 * size
    shift = t.base - base
    if small and shift < -0.12 * size:
        return "sup"
    if small and shift > 0.08 * size:
        return "sub"
    if not small and shift < -0.32 * size and t.math:
        return "sup"
    if not small and shift > 0.22 * size and t.math:
        return "sub"
    return "base"


def build_latex(toks: list[Tok]) -> str:
    """LaTeX for tokens forming one horizontal row (scripts detected against the row baseline)."""
    toks = sorted((t for t in toks), key=lambda t: (t.x0, t.y0))
    toks = [t for t in toks if not t.space]
    if not toks:
        return ""
    base, size = _row_metrics(toks)
    parts: list[str] = []
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        kind = _script_kind(t, base, size)
        if kind == "base":
            # upright multi-letter words inside math -> \mathrm{} / operator names
            if t.letters and not t.italic:
                j = i
                word = []
                while j < n and toks[j].letters and not toks[j].italic and _script_kind(toks[j], base, size) == "base" \
                        and (j == i or toks[j].x0 - toks[j - 1].x1 < 0.15 * size):
                    word.append(toks[j])
                    j += 1
                w = "".join(x.text for x in word)
                if len(w) >= 2:
                    parts.append("\\" + w if w in _FUNCTIONS else rf"\mathrm{{{w}}}")
                    i = j
                    continue
            if t.accent:
                acx = (t.x0 + t.x1) / 2  # accent centre
                fwd_ok = (i + 1 < n and _overlap_x(toks[i + 1], t.x0 - 0.5, t.x1 + 0.5) > 0
                          and not toks[i + 1].accent)
                bwd_ok = (parts and i > 0
                          and _overlap_x(toks[i - 1], t.x0 - 0.5, t.x1 + 0.5) > 0)
                if fwd_ok and bwd_ok:
                    # both overlap: pick the one whose centre is closer to the accent
                    fwd_cx = (toks[i + 1].x0 + toks[i + 1].x1) / 2
                    bwd_cx = (toks[i - 1].x0 + toks[i - 1].x1) / 2
                    bwd_ok = abs(bwd_cx - acx) <= abs(fwd_cx - acx)
                    fwd_ok = not bwd_ok
                if bwd_ok:
                    parts[-1] = f"{t.accent}{{{parts[-1]}}}"
                    i += 1
                    continue
                if fwd_ok:
                    parts.append(f"{t.accent}{{{toks[i + 1].latex}}}")
                    i += 2
                    continue
            if t.text == _NEGATION and i + 1 < n:
                nxt = toks[i + 1]
                parts.append(_NEGATED.get(nxt.latex, rf"\not{nxt.latex}"))
                i += 2
                continue
            if i + 1 < n and toks[i + 1].text == _NEGATION and _overlap_x(toks[i + 1], t.x0, t.x1) > 0:
                parts.append(_NEGATED.get(t.latex, rf"\not{t.latex}"))
                i += 2
                continue
            parts.append(t.latex)
            i += 1
            continue
        # a run of scripts: split into sub and sup groups (they may be stacked)
        j = i
        subs: list[Tok] = []
        sups: list[Tok] = []
        while j < n:
            k = _script_kind(toks[j], base, size)
            if k == "base":
                break
            (sups if k == "sup" else subs).append(toks[j])
            j += 1
        if not parts or parts[-1] in ("", " "):
            parts.append("{}")
        tail = ""
        if subs:
            tail += "_{" + build_latex(subs) + "}"
        if sups:
            primes = "".join(s.latex for s in sups)
            if primes and set(primes) == {"'"}:
                tail += primes
            else:
                tail += "^{" + build_latex(sups) + "}"
        parts[-1] = parts[-1] + tail
        i = j
    return _join(parts)


# ----------------------------------------------------------------------------------------------
# page reconstruction
# ----------------------------------------------------------------------------------------------


def page_has_math_fonts(page_dict: dict) -> bool:
    for b in page_dict.get("blocks", []):
        for line in b.get("lines", []):
            for s in line.get("spans", []):
                text = s.get("text", "")
                # list bullets are commonly set in a Symbol font: not evidence of math
                if MATH_FONT.search(s.get("font", "")) and text.strip(" \t •·▪◦●■□–—-*"):
                    return True
    return False


def _tokens(raw: dict) -> tuple[list[Tok], float]:
    toks: list[Tok] = []
    sizes: list[float] = []
    for bi, b in enumerate(raw.get("blocks", [])):
        if b.get("type", 0) != 0:
            continue
        for li, line in enumerate(b.get("lines", [])):
            for s in line.get("spans", []):
                font = s.get("font", "")
                size = float(s.get("size", 0) or 0)
                flags = int(s.get("flags", 0) or 0)
                is_math_font = not _is_prose_font(font)
                italic = bool(flags & 2) or bool(re.search(r"ital|-it\b|oblique|ti\d", font, re.I))
                bold = bool(flags & 16) or bool(re.search(r"bold|bx\d", font, re.I))
                for ch in s.get("chars", []):
                    c = ch.get("c", "")
                    x0, y0, x1, y1 = ch["bbox"]
                    ox, oy = ch["origin"]
                    if c.isspace() or c == "":
                        toks.append(Tok(" ", x0, y0, x1, y1, oy, size, False, " ", bi, li, font, space=True))
                        continue
                    latex, big = _char_latex(c, font)
                    accent = ""
                    if c in _ACCENTS and (is_math_font or c in "¯ˆ˜˙¨"):
                        accent = _ACCENTS[c]
                    letters = c.isalpha() and not is_math_font and c not in _SYMBOLS
                    toks.append(Tok(latex, x0, y0, x1, y1, oy, size, is_math_font, c, bi, li, font,
                                    big_op=big, accent=accent, letters=letters, italic=italic or is_math_font,
                                    bold=bold))
                    if not is_math_font and c.isalpha():
                        sizes.append(size)
    body = statistics.median(sizes) if sizes else 10.0
    return toks, body


def _consume_structures(toks: list[Tok], bars: list[tuple[float, float, float, float]], body: float) -> list[Tok]:
    """Replace radicals, fractions and large-operator limits by single structural tokens."""
    alive = [t for t in toks if not t.space]
    spaces = [t for t in toks if t.space]

    def take(pred) -> list[Tok]:
        got = [t for t in alive if pred(t)]
        for t in got:
            alive.remove(t)
        return got

    # 1. radicals and fractions: shortest rule first, so inner structures are built first
    for x0, x1, y, _thick in sorted(bars, key=lambda b: b[1] - b[0]):
        w = x1 - x0
        radical = next((t for t in alive if t.latex == r"\surd" and abs(t.x1 - x0) <= 2.0
                        and t.y0 - 2.5 <= y <= t.y0 + 0.35 * (t.y1 - t.y0)), None)
        if radical is not None:
            inner = take(lambda t: t is not radical and t.cx >= x0 - 0.5 and t.cx <= x1 + 0.5
                         and (t.y0 >= y - 1.0 or t.base >= y + 1.0) and t.base <= radical.y1 + 14.0 and t.y0 < radical.y1 + 6.0)
            if inner:
                alive.remove(radical)
                inner_latex = build_latex(inner)
                rb, rs = _row_metrics(inner)
                alive.append(Tok(rf"\sqrt{{{inner_latex}}}", radical.x0, min(y, radical.y0), x1,
                                 max(radical.y1, max(t.y1 for t in inner)), rb, rs, True,
                                 block=radical.block, line=radical.line,
                                 source_blocks=frozenset.union(radical.source_blocks, *(t.source_blocks for t in inner))))
            continue
        near = min(1.6 * body, 1.5 * w + 1.0)
        x_margin = 1.0
        overhang = 2.5

        def is_num(t: Tok) -> bool:
            return (x0 - x_margin <= t.cx <= x1 + x_margin and t.base <= y + 0.5 and t.base >= y - near
                    and t.y1 <= y + 0.35 * t.size)

        def is_den(t: Tok) -> bool:
            return (x0 - x_margin <= t.cx <= x1 + x_margin and t.y0 >= y - 0.35 * t.size and t.base > y
                    and t.base <= y + near + 0.6 * body)

        num = [t for t in alive if is_num(t)]
        den = [t for t in alive if is_den(t)]
        if not num or not den:
            continue
        # a fraction's parts sit within the rule; prose on the line above/below would overhang it
        if any(t.x0 < x0 - overhang or t.x1 > x1 + overhang for t in num + den):
            continue
        nspan = max(t.x1 for t in num) - min(t.x0 for t in num)
        dspan = max(t.x1 for t in den) - min(t.x0 for t in den)
        if max(nspan, dspan) < 0.45 * w:
            continue
        # keep only the row of the numerator closest to the rule (and the denominator likewise)
        nb = max(t.base for t in num if t.size >= 0.86 * max(u.size for u in num))
        db = min(t.base for t in den if t.size >= 0.86 * max(u.size for u in den))
        num = [t for t in num if t.base >= nb - 0.9 * body]
        den = [t for t in den if t.base <= db + 0.9 * body]
        for t in num + den:
            alive.remove(t)
        size = max(t.size for t in num + den)
        frac = Tok(rf"\frac{{{build_latex(num)}}}{{{build_latex(den)}}}", x0, min(t.y0 for t in num), x1,
                   max(t.y1 for t in den), y + 0.25 * size, size, True,
                   block=min(t.block for t in num + den), line=num[0].line,
                   source_blocks=frozenset.union(*(t.source_blocks for t in num + den)))
        alive.append(frac)

    # 2. limits above / below large operators
    for op in [t for t in alive if t.big_op]:
        h = max(op.y1 - op.y0, op.size)
        span = (op.x0 - 0.6 * op.width - 2.0, op.x1 + 0.6 * op.width + 2.0)

        def above(t: Tok) -> bool:
            return (t is not op and t.size < op.size * 0.9 and span[0] <= t.cx <= span[1]
                    and t.y1 <= op.y0 + 0.25 * h and t.y0 >= op.y0 - 1.3 * body)

        def below(t: Tok) -> bool:
            return (t is not op and t.size < op.size * 0.9 and span[0] <= t.cx <= span[1]
                    and t.y0 >= op.y1 - 0.25 * h and t.y1 <= op.y1 + 1.3 * body)

        up = take(above)
        down = take(below)
        if not up and not down:
            continue
        alive.remove(op)
        latex = op.latex
        if down:
            latex += "_{" + build_latex(down) + "}"
        if up:
            latex += "^{" + build_latex(up) + "}"
        # the operator sits on the row's math axis: give it a baseline the row will accept
        alive.append(Tok(latex, min([op.x0] + [t.x0 for t in up + down]), op.y0,
                         max([op.x1] + [t.x1 for t in up + down]), op.y1,
                         (op.y0 + op.y1) / 2 + 0.3 * body, body, True, block=op.block, line=op.line,
                         big_op=False,
                         source_blocks=frozenset.union(op.source_blocks, *(t.source_blocks for t in up + down))))
    return alive + spaces


def _rows(toks: list[Tok], body: float) -> list[list[Tok]]:
    """Group tokens into visual rows: PyMuPDF lines joined when they sit side by side."""
    lines: dict[tuple[int, int], list[Tok]] = {}
    for t in toks:
        lines.setdefault((t.block, t.line), []).append(t)
    keys = list(lines)
    info = {}
    for k in keys:
        ts = [t for t in lines[k] if not t.space]
        if not ts:
            info[k] = None
            continue
        b, s = _row_metrics(ts)
        mathy = all(t.math or not t.letters for t in ts)
        info[k] = (min(t.x0 for t in ts), max(t.x1 for t in ts), b, s, mathy,
                   min(t.y0 for t in ts), max(t.y1 for t in ts))
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    live = [k for k in keys if info[k] is not None]
    live.sort(key=lambda k: info[k][2])
    for i, a in enumerate(live):
        ax0, ax1, ab, as_, am, ay0, ay1 = info[a]
        for b in live[i + 1:]:
            bx0, bx1, bb, bs, bm, by0, by1 = info[b]
            if bb - ab > 1.2 * max(as_, bs, body):
                break
            big, small = (info[a], info[b]) if as_ >= bs else (info[b], info[a])
            d = small[2] - big[2]
            if not (-0.75 * big[3] <= d <= 0.5 * big[3]):
                continue
            gap = max(bx0 - ax1, ax0 - bx1)
            limit = 2.2 * body if (am and bm) else 0.6 * body
            if gap > limit:
                continue
            if gap < -0.5 and abs(d) < 0.12 * big[3] and min(ax1, bx1) - max(ax0, bx0) > 2.0 and as_ == bs:
                continue  # same baseline but overlapping in x: two separate text lines (e.g. columns)
            parent[find(a)] = find(b)
    groups: dict = {}
    for k in keys:
        if info[k] is None:
            continue
        groups.setdefault(find(k), []).extend(lines[k])
    for k in keys:
        if info[k] is None:  # whitespace-only line: attach to nothing
            continue
    return list(groups.values())


def _words(row: list[Tok], size: float) -> list[list[Tok]]:
    """Split a row into words at spaces and at wide gaps."""
    toks = sorted(row, key=lambda t: (t.x0, t.y0))
    words: list[list[Tok]] = []
    cur: list[Tok] = []
    prev = None
    for t in toks:
        if t.space:
            if cur:
                words.append(cur)
                cur = []
            prev = None
            continue
        if prev is not None and t.x0 - prev.x1 > 0.28 * size and not (t.math and prev.math):
            words.append(cur)
            cur = []
        cur.append(t)
        prev = t if prev is None or t.x1 >= prev.x1 else prev
    if cur:
        words.append(cur)
    return words


def _word_class(w: list[Tok], base: float, size: float) -> str:
    """'math' | 'neutral' | 'prose'."""
    if any(t.math for t in w):
        return "math"
    if any(_script_kind(t, base, size) != "base" for t in w):
        letters = "".join(t.text for t in w if t.letters)
        if letters and not any(t.math for t in w) and len(letters) >= 3:
            return "prose"
        return "math"
    letters = "".join(t.text for t in w if t.letters)
    if not letters:
        return "neutral"
    stripped = "".join(t.text for t in w).strip(".,;:()")
    if stripped in _FUNCTIONS:
        return "neutral"
    if len(letters) == 1 and len(stripped) == 1 and (w[0].italic or w[0].bold):
        return "neutral"
    return "prose"


def _split_word(w: list[Tok], base: float, size: float) -> list[list[Tok]]:
    """A word such as "δ)-differential" holds math and prose: split at the prose letters."""
    if not any(t.math for t in w):
        return [w]
    out: list[list[Tok]] = []
    cur: list[Tok] = []
    i = 0
    n = len(w)
    while i < n:
        if w[i].letters and _script_kind(w[i], base, size) == "base":
            j = i
            while j < n and w[j].letters and _script_kind(w[j], base, size) == "base":
                j += 1
            run = "".join(t.text for t in w[i:j])
            if len(run) >= 3 and run not in _FUNCTIONS and not (j < n and w[j].text in "(_"):
                # prose letters; a hyphen just before them belongs to the prose
                if cur and cur[-1].text in "-‐":
                    hy = cur.pop()
                    if cur:
                        out.append(cur)
                    cur = [hy]
                elif cur:
                    out.append(cur)
                    cur = []
                out.append(cur + w[i:j])
                cur = []
                i = j
                continue
        cur.append(w[i])
        i += 1
    if cur:
        out.append(cur)
    return [p for p in out if p]


def _prose(w: list[Tok]) -> str:
    return "".join(t.text for t in sorted(w, key=lambda t: t.x0))


_EQ_NUMBER = re.compile(r"^\(\s*[0-9]+(\.[0-9]+)*[a-z]?\s*\)$|^\([A-Z]?\d+(\.\d+)*\)$")


def _render_row(row: list[Tok], body: float) -> tuple[str, int, int, bool]:
    """Text of one row with math as $...$ (or the whole row as $$...$$).

    Returns (text, inline_count, display_count, had_math)."""
    nonspace = [t for t in row if not t.space]
    base, size = _row_metrics(nonspace)
    words = []
    for w in _words(row, size):
        words.extend(_split_word(w, base, size))
    classes = [_word_class(w, base, size) for w in words]
    if "math" not in classes:
        return " ".join(_prose(w) for w in words), 0, 0, False

    # equation number at the far right of a display row
    eqno = ""
    if len(words) >= 2 and classes[-1] != "math" and _EQ_NUMBER.match(_prose(words[-1])) \
            and words[-1][0].x0 - max(t.x1 for t in words[-2]) > 2.0 * size:
        eqno = _prose(words[-1])
        words, classes = words[:-1], classes[:-1]

    prose_words = [c for c in classes if c == "prose"]
    if not prose_words:
        toks = [t for w in words for t in w]
        latex = build_latex(toks).strip()
        latex = re.sub(r"(?<![.])\.\.\.(?=[^.])", r"\\ldots ", latex)
        # sentence punctuation after a display equation is part of the equation in TeX source
        text = f"$${latex}$$" + (f" {eqno}" if eqno else "")
        return text, 0, 1, True

    out: list[str] = []
    i = 0
    n = len(words)
    inline = 0
    while i < n:
        if classes[i] == "prose":
            out.append(_prose(words[i]))
            i += 1
            continue
        # a maximal run of math / neutral words, trimmed to start and end on math or neutral symbols
        j = i
        while j < n and classes[j] != "prose":
            j += 1
        run = words[i:j]
        rc = classes[i:j]
        if "math" not in rc:
            out.extend(_prose(w) for w in run)
            i = j
            continue
        # leading / trailing neutral words: keep digits and operators, drop plain sentence numbers
        lo, hi = 0, len(run)
        while lo < hi and rc[lo] == "neutral" and not _attachable(run[lo]):
            out.append(_prose(run[lo]))
            lo += 1
        tail: list[str] = []
        while hi > lo and rc[hi - 1] == "neutral" and not _attachable(run[hi - 1]):
            tail.insert(0, _prose(run[hi - 1]))
            hi -= 1
        toks = [t for w in run[lo:hi] for t in w]
        # trailing sentence punctuation stays outside the math
        trail = ""
        toks.sort(key=lambda t: t.x0)
        while toks and toks[-1].text in ".,;:" and not toks[-1].math and _script_kind(toks[-1], base, size) == "base":
            trail = toks[-1].text + trail
            toks.pop()
        lead = ""
        while toks and toks[0].text in "([" and not toks[0].math and not _balanced_open(toks):
            lead += toks[0].text
            toks.pop(0)
        latex = build_latex(toks).strip()
        # three consecutive periods → \ldots  (common in TeX PDFs)
        latex = re.sub(r"(?<![.])\.\.\.(?=[^.])", r"\\ldots ", latex)
        if latex:
            out.append(f"{lead}${latex}${trail}")
            inline += 1
        else:
            out.append(lead + trail)
        out.extend(tail)
        i = j
    return " ".join(p for p in out if p), inline, 0, True


def _attachable(w: list[Tok]) -> bool:
    """A neutral word next to math joins it, unless it is a citation such as [12] or [3, 4]."""
    s = _prose(w)
    return not re.fullmatch(r"\[\d+(?:[,–-]\s*\d+)*\][.,;:]?", s)


def _balanced_open(toks: list[Tok]) -> bool:
    s = "".join(t.text for t in toks)
    return s.count("(") <= s.count(")") and s.count("[") <= s.count("]")


def rebuild_page(raw: dict, drawings: list[dict], page_width: float, *,
                 excluded_rects=(), excluded_rule_rects=()) -> PageMath:
    """Rebuilt text for every block that contains math, from a PyMuPDF rawdict."""
    toks, body = _tokens(raw)
    # Word ownership follows the extractor's full-containment decision. Do not
    # discard an entire partially overlapping block or consume a table rule as
    # a fraction bar. Default-empty exclusions preserve the committed math API.
    def owned(box, regions):
        x0, y0, x1, y1 = box
        return any(r[0] - 1 <= x0 and r[1] - 1 <= y0 and
                   x1 <= r[2] + 1 and y1 <= r[3] + 1 for r in regions)
    toks = [t for t in toks if not owned((t.x0, t.y0, t.x1, t.y1), excluded_rects)]
    bars = [b for b in _bars(drawings, page_width)
            if not owned((b[0], b[2], b[1], b[2]), excluded_rule_rects)]
    result = PageMath()
    if not any(t.math for t in toks):
        return result
    toks = _consume_structures(toks, bars, body)
    rows = _rows(toks, body)
    rendered: list[tuple[int, float, float, str, bool, bool]] = []  # block, y, x, text, display, math
    math_blocks: set[int] = set()
    row_owners: list[set[int]] = []
    for row in rows:
        nonspace = [t for t in row if not t.space]
        if not nonspace:
            continue
        text, n_inline, n_display, had_math = _render_row(row, body)
        counts: dict[int, int] = {}
        for t in nonspace:
            for source_block in t.source_blocks:
                counts[source_block] = counts.get(source_block, 0) + 1
        row_owners.append(set(counts))
        anchor = max(counts, key=lambda b: (counts[b], -b))
        y = min(t.y0 for t in nonspace)
        x = min(t.x0 for t in nonspace)
        rendered.append((anchor, y, x, text, n_display > 0, had_math))
        if had_math:
            math_blocks.update(counts)
            result.inline_count += n_inline
            result.display_count += n_display
    if not math_blocks:
        return result
    # A reconstructed block replaces its entire original. Transitively include
    # every block sharing one of its rows, even a prose-only row, so moved prose
    # is neither suppressed nor emitted a second time by the plain-text fallback.
    changed = True
    while changed:
        changed = False
        for owners in row_owners:
            if owners & math_blocks and not owners <= math_blocks:
                math_blocks.update(owners)
                changed = True
    by_block: dict[int, list] = {}
    for anchor, y, x, text, display, had_math in rendered:
        by_block.setdefault(anchor, []).append((y, x, text, display))
    for b in math_blocks:
        result.source_blocks[b] = sorted(set().union(*(
            owners for row, owners in zip(rendered, row_owners) if row[0] == b)))
        items = sorted(by_block.get(b, []))
        parts: list[str] = []
        for y, x, text, display in items:
            if display:
                parts.append("\n" + text + "\n")
            else:
                parts.append(text)
        content = " ".join(parts)
        content = re.sub(r"[ \t]*\n[ \t]*", "\n", content)
        content = re.sub(r"\n{2,}", "\n", content).strip()
        result.blocks[b] = content
    return result
