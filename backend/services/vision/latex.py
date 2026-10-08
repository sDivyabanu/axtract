"""LaTeX helpers: strict validation, inline-equation detection, OCR cross-check.

`validate_latex` is intentionally stricter than a MathML converter: unbalanced groups,
unknown commands and unmatched \\left/\\right or environments are all rejected, because an
equation that does not parse must never be reported with high confidence.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from latex2mathml.converter import convert as _latex_to_mathml

# Commands KaTeX/MathJax support that we expect in extracted math.
_KNOWN = set(
    """
alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi
rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma
Upsilon Phi Psi Omega
frac dfrac tfrac cfrac binom dbinom tbinom sqrt root sum prod coprod int iint iiint oint oiint bigcup
bigcap bigvee bigwedge bigoplus bigotimes lim limsup liminf sup inf max min arg det dim exp gcd hom
ker lg ln log Pr sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth deg mod bmod pmod
pm mp times div cdot cdots ldots dots vdots ddots circ bullet ast star oplus ominus otimes oslash
leq le geq ge neq ne approx equiv sim simeq cong propto ll gg prec succ preceq succeq subset supset
subseteq supseteq nsubseteq in notin ni cup cap setminus emptyset varnothing forall exists nexists
neg lnot land lor wedge vee implies iff to rightarrow leftarrow leftrightarrow Rightarrow Leftarrow
Leftrightarrow mapsto longrightarrow longleftarrow uparrow downarrow infty partial nabla angle
perp parallel mid nmid hbar ell Re Im aleph prime backslash
left right big Big bigg Bigg langle rangle lbrace rbrace lceil rceil lfloor rfloor vert Vert lvert
rvert lVert rVert
hat widehat tilde widetilde bar overline underline vec overrightarrow overleftarrow dot ddot
dddot acute grave breve check mathring overbrace underbrace overset underset stackrel
text textbf textit textrm mathrm mathbf mathit mathsf mathtt mathcal mathbb mathfrak boldsymbol
operatorname displaystyle textstyle scriptstyle limits nolimits
quad qquad hspace thinspace medspace thickspace negthinspace phantom hphantom vphantom
begin end hline cr not boxed cancel xcancel color
space tag label
""".split()
)
_SPACING_SYMBOLS = set(",;:! ")  # \, \; \: \! \<space>
_ESCAPED_SYMBOLS = set("{}%$&#_|\\")
_ENV_NAMES = {
    "matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "cases", "aligned",
    "align", "array", "split", "gathered", "smallmatrix", "alignedat",
}
_CMD_RE = re.compile(r"\\([A-Za-z]+|.)")


@dataclass
class Validation:
    ok: bool
    errors: list[str] = field(default_factory=list)


def validate_latex(latex: str) -> Validation:
    """Strictly validate a LaTeX math string."""
    errors: list[str] = []
    s = (latex or "").strip()
    if not s:
        return Validation(False, ["empty"])

    # 1. braces balance (ignoring escaped \{ \})
    depth = 0
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth < 0:
                errors.append("unbalanced '}'")
                break
        i += 1
    if depth > 0:
        errors.append("unclosed '{'")

    # 2. commands, \left/\right, environments
    left_right = 0
    env_stack: list[str] = []
    for m in _CMD_RE.finditer(s):
        name = m.group(1)
        if len(name) == 1 and not name.isalpha():
            if name not in _SPACING_SYMBOLS and name not in _ESCAPED_SYMBOLS and name not in "()[]":
                errors.append(f"unknown escape \\{name}")
            continue
        if name == "left":
            left_right += 1
        elif name == "right":
            left_right -= 1
            if left_right < 0:
                errors.append("\\right without \\left")
        if name not in _KNOWN:
            errors.append(f"unknown command \\{name}")
    if left_right > 0:
        errors.append("\\left without \\right")
    for m in re.finditer(r"\\(begin|end)\{([^}]*)\}", s):
        kind, env = m.groups()
        if env not in _ENV_NAMES:
            errors.append(f"unknown environment {env}")
        if kind == "begin":
            env_stack.append(env)
        elif not env_stack or env_stack.pop() != env:
            errors.append(f"mismatched \\end{{{env}}}")
    if env_stack:
        errors.append("unclosed environment")

    # 3. dangling operators / empty required arguments
    if re.search(r"\\(frac|dfrac|tfrac|binom)\s*(\{\s*\}|$)", s):
        errors.append("\\frac without arguments")
    if re.search(r"[\^_]\s*$", s) or re.search(r"[\^_]\s*[}&]", s):
        errors.append("dangling ^ or _")
    if re.search(r"[\^_]\s*\{\s*\}", s):
        errors.append("empty ^ or _ group")
    if re.search(r"(\^|_)\s*(\^|_)", s):
        errors.append("double ^ or _")

    # 4. final syntax pass with a real parser
    if not errors:
        try:
            _latex_to_mathml(s)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"parse error: {type(exc).__name__}")
    return Validation(not errors, errors)


# ---------------------------------------------------------------------------
# Plain (text-layer) math -> LaTeX, only for unambiguous inline equations
# ---------------------------------------------------------------------------

_FUNCS = {"sin", "cos", "tan", "log", "ln", "exp", "sqrt", "min", "max", "lim"}
_VAR_RE = re.compile(r"^[A-Za-z]{1,3}([\^_](\{[\w+\-]+\}|[\w+\-]+))?$")
_NUM_RE = re.compile(r"^\d+([.,]\d+)?([\^_](\{[\w+\-]+\}|[\w+\-]+))?$")
_ALGEBRA_TERM_RE = re.compile(r"^\d+(?:[.,]\d+)?[A-Za-z]{1,3}([\^_](\{[\w+\-]+\}|[\w+\-]+))?$")
_OP_RE = re.compile(r"^(=|\+|-|\*|/|\^|<|>|<=|>=|≤|≥|≠|±|×|÷|·|\(|\)|[\(\)]?[A-Za-z0-9]+[\)]?)$")
_REL = {"=", "<", ">", "<=", ">=", "≤", "≥", "≠"}
_OPERATORS = {"+", "-", "−", "*", "/", "×", "÷", "·", "±"}


# Short English words that look like 1-3 letter variables but are prose.
_STOPWORDS = frozenset(
    "the and or in is are of to for on at as by if it we be so no not can has was let any all one two "
    "nor but yet its his her our out off per via who how why now then when with that this than from "
    "into over also each both such more most some been were will may use".split()
)


def _is_math_token(tok: str) -> bool:
    t = tok.strip("(),;")
    if not t:
        return True
    if t.lower() in _STOPWORDS:
        return False
    if t in _REL or t in _OPERATORS:
        return True
    if _NUM_RE.match(t) or _VAR_RE.match(t) or _ALGEBRA_TERM_RE.match(t):
        return True
    return False


def find_inline_equations(text: str) -> list[str]:
    """Find equation-like runs inside running text, e.g. 'x^2 + y^2 = r^2'.

    Conservative: a run of math tokens (single-letter variables, numbers, operators,
    exponents/subscripts) that contains a relation sign and at least one other token on
    each side. Ordinary words never count.
    """
    tokens = text.split()
    runs: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        # strip sentence punctuation that is not part of the math
        if _is_math_token(tok) and not (len(tok) > 3 and tok.isalpha()):
            cur.append(tok)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    found: list[str] = []
    for run in runs:
        # trim leading/trailing bare words that are only variables next to prose
        idx = [i for i, t in enumerate(run) if t in _REL]
        if not idx:
            continue
        first, last = idx[0], idx[-1]
        if first == 0 or last == len(run) - 1:
            continue
        # drop trailing/leading operators
        lo, hi = 0, len(run)
        while lo < hi and run[lo] in _OPERATORS:
            lo += 1
        while hi > lo and run[hi - 1] in _OPERATORS:
            hi -= 1
        seg = run[lo:hi]
        # require some math content on both sides: operator, exponent, or digit
        joined = " ".join(seg)
        if len(seg) >= 3 and re.search(r"[\^_+*/\d]|[A-Za-z]\s*[+\-]", joined):
            found.append(joined)
    return found


def plain_to_latex(expr: str) -> str:
    """Deterministic plain-text -> LaTeX for the token set accepted by find_inline_equations."""
    s = expr.replace("−", "-")
    s = re.sub(r"([A-Za-z0-9\)])\^(-?\w+)", lambda m: f"{m.group(1)}^{{{m.group(2)}}}", s)
    s = re.sub(r"([A-Za-z0-9\)])_(-?\w+)", lambda m: f"{m.group(1)}_{{{m.group(2)}}}", s)
    s = s.replace("<=", r"\leq ").replace(">=", r"\geq ").replace("≤", r"\leq ").replace("≥", r"\geq ")
    s = s.replace("≠", r"\neq ").replace("×", r"\times ").replace("·", r"\cdot ").replace("÷", r"\div ")
    s = s.replace("±", r"\pm ").replace("*", r"\cdot ")
    return " ".join(s.split())


# ---------------------------------------------------------------------------
# Cross-check formula model output against an independent OCR read
# ---------------------------------------------------------------------------

_LATEX_WORDS = re.compile(r"\\[A-Za-z]+")
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "0123456789" * 2)
_GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "π": "pi", "σ": "sigma", "θ": "theta",
    "λ": "lambda", "μ": "mu", "Σ": "sum", "∑": "sum", "∫": "int", "√": "sqrt", "∞": "infty",
}


def _alnum_counter(text: str) -> Counter:
    text = text.translate(_SUPERSCRIPT)
    for ch, name in _GREEK.items():
        text = text.replace(ch, f" \\{name} ")
    text = _LATEX_WORDS.sub(" ", text)  # commands carry no alphanumerics to compare
    text = unicodedata.normalize("NFKD", text)
    return Counter(c.lower() for c in text if c.isalnum())


def crosscheck(latex: str, ocr_text: str) -> float:
    """Overlap (0-1) between the letters/digits of the LaTeX and an independent OCR read."""
    a, b = _alnum_counter(latex), _alnum_counter(ocr_text)
    if not a or not b:
        return 0.0
    inter = sum((a & b).values())
    return inter / max(sum(a.values()), sum(b.values()))


_MATHY = re.compile(r"=|\\frac|\\sqrt|\\sum|\\int|\\prod|\\lim|\^|_|\\[a-zA-Z]+")


def looks_mathy(latex: str) -> bool:
    """True when the string has real math structure (not just a line of words)."""
    return bool(_MATHY.search(latex or ""))
