"""OMML (Office Math Markup Language) -> LaTeX.

Word stores equations as <m:oMath>/<m:oMathPara>; PowerPoint stores the same markup under
<a14:m>. This is a self-contained converter (no third-party code, no network). Constructs it
does not understand are reported in `Converted.unsupported`; their text content is kept but
the caller lowers confidence and flags the block for review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_M = f"{{{M_NS}}}"


def _t(el: Element) -> str:
    """Local tag name."""
    return el.tag.split("}")[-1] if isinstance(el.tag, str) else ""


def _child(el: Element, name: str) -> Element | None:
    for c in el:
        if _t(c) == name:
            return c
    return None


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if _t(c) == name]


def _val(el: Element | None, default: str | None = None) -> str | None:
    if el is None:
        return default
    return el.get(f"{_M}val", el.get("val", default))


# Unicode math characters -> LaTeX
_CHAR_MAP = {
    "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ", "ε": r"\epsilon ", "ϵ": r"\epsilon ",
    "ζ": r"\zeta ", "η": r"\eta ", "θ": r"\theta ", "ϑ": r"\vartheta ", "ι": r"\iota ", "κ": r"\kappa ",
    "λ": r"\lambda ", "μ": r"\mu ", "ν": r"\nu ", "ξ": r"\xi ", "π": r"\pi ", "ρ": r"\rho ",
    "σ": r"\sigma ", "ς": r"\varsigma ", "τ": r"\tau ", "υ": r"\upsilon ", "φ": r"\phi ", "ϕ": r"\varphi ",
    "χ": r"\chi ", "ψ": r"\psi ", "ω": r"\omega ",
    "Γ": r"\Gamma ", "Δ": r"\Delta ", "Θ": r"\Theta ", "Λ": r"\Lambda ", "Ξ": r"\Xi ", "Π": r"\Pi ",
    "Σ": r"\Sigma ", "Φ": r"\Phi ", "Ψ": r"\Psi ", "Ω": r"\Omega ",
    "±": r"\pm ", "∓": r"\mp ", "×": r"\times ", "÷": r"\div ", "·": r"\cdot ", "∙": r"\cdot ", "⋅": r"\cdot ",
    "∘": r"\circ ", "∗": r"\ast ", "−": "-", "–": "-",
    "≤": r"\leq ", "≥": r"\geq ", "≠": r"\neq ", "≈": r"\approx ", "≡": r"\equiv ", "∼": r"\sim ",
    "≅": r"\cong ", "∝": r"\propto ", "≪": r"\ll ", "≫": r"\gg ",
    "∈": r"\in ", "∉": r"\notin ", "∋": r"\ni ", "⊂": r"\subset ", "⊃": r"\supset ", "⊆": r"\subseteq ",
    "⊇": r"\supseteq ", "∪": r"\cup ", "∩": r"\cap ", "∖": r"\setminus ", "∅": r"\emptyset ",
    "∀": r"\forall ", "∃": r"\exists ", "¬": r"\neg ", "∧": r"\wedge ", "∨": r"\vee ",
    "→": r"\to ", "←": r"\leftarrow ", "↔": r"\leftrightarrow ", "⇒": r"\Rightarrow ",
    "⇐": r"\Leftarrow ", "⇔": r"\Leftrightarrow ", "↦": r"\mapsto ",
    "∞": r"\infty ", "∂": r"\partial ", "∇": r"\nabla ", "ℏ": r"\hbar ", "ℓ": r"\ell ", "ℜ": r"\Re ",
    "ℑ": r"\Im ", "ℵ": r"\aleph ", "′": "'", "∠": r"\angle ", "⊥": r"\perp ", "∥": r"\parallel ",
    "…": r"\ldots ", "⋯": r"\cdots ", "⋮": r"\vdots ", "⋱": r"\ddots ", "√": r"\sqrt{}",
    "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ", "∬": r"\iint ", "∭": r"\iiint ", "∮": r"\oint ",
    "⋃": r"\bigcup ", "⋂": r"\bigcap ", "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    "{": r"\{", "}": r"\}", "#": r"\#", "%": r"\%", "&": r"\&", "$": r"\$", "\\": r"\backslash ",
}
_NARY = {
    "∑": r"\sum", "∏": r"\prod", "∐": r"\coprod", "∫": r"\int", "∬": r"\iint", "∭": r"\iiint",
    "∮": r"\oint", "⋃": r"\bigcup", "⋂": r"\bigcap", "⋁": r"\bigvee", "⋀": r"\bigwedge",
}
_FUNCS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh",
    "coth", "log", "ln", "lg", "exp", "min", "max", "sup", "inf", "lim", "det", "dim", "gcd", "deg",
    "ker", "arg", "Pr",
}
_ACCENTS = {
    "̂": r"\hat", "^": r"\hat", "̃": r"\tilde", "~": r"\tilde", "̄": r"\bar", "¯": r"\bar",
    "⃗": r"\vec", "→": r"\vec", "̇": r"\dot", "˙": r"\dot", "̈": r"\ddot", "¨": r"\ddot",
    "́": r"\acute", "´": r"\acute", "̀": r"\grave", "`": r"\grave", "̌": r"\check",
    "ˇ": r"\check", "̆": r"\breve", "˘": r"\breve",
}
_DELIMS = {"": r".", "{": r"\{", "}": r"\}", "⟨": r"\langle", "⟩": r"\rangle", "‖": r"\|",
           "⌈": r"\lceil", "⌉": r"\rceil", "⌊": r"\lfloor", "⌋": r"\rfloor", "|": "|", "(": "(",
           ")": ")", "[": "[", "]": "]", "〈": r"\langle", "〉": r"\rangle"}


@dataclass
class Converted:
    latex: str
    display: bool = False
    unsupported: set[str] = field(default_factory=set)


class _Converter:
    def __init__(self) -> None:
        self.unsupported: set[str] = set()

    # -- text ------------------------------------------------------------
    def _text(self, s: str) -> str:
        out = []
        for ch in s:
            out.append(_CHAR_MAP.get(ch, ch))
        return "".join(out)

    def _run(self, r: Element) -> str:
        text = "".join((t.text or "") for t in r if _t(t) == "t")
        if not text:
            return ""
        rpr = _child(r, "rPr")
        plain = _val(_child(rpr, "sty")) == "p" if rpr is not None else False
        normal = _val(_child(rpr, "nor")) in ("1", "on", "true") if rpr is not None else False
        bold = _val(_child(rpr, "sty")) in ("b", "bi") if rpr is not None else False
        if normal:
            return r"\text{" + text.replace("\\", "") + "}"
        body = self._text(text)
        if plain and text.isalpha() and len(text) > 1:
            body = r"\mathrm{" + text + "}"
        if bold:
            body = r"\mathbf{" + body.strip() + "}"
        return body

    # -- generic ---------------------------------------------------------
    def seq(self, el: Element | None) -> str:
        if el is None:
            return ""
        return "".join(self.node(c) for c in el)

    def arg(self, el: Element | None) -> str:
        s = self.seq(el).strip()
        return s if s else ""

    def node(self, el: Element) -> str:
        name = _t(el)
        fn = getattr(self, f"_n_{name}", None)
        if fn is not None:
            return fn(el)
        if name in ("rPr", "ctrlPr", "fPr", "sSupPr", "sSubPr", "sSubSupPr", "radPr", "naryPr", "dPr",
                    "accPr", "barPr", "funcPr", "limLowPr", "limUppPr", "mPr", "eqArrPr", "groupChrPr",
                    "boxPr", "phantPr", "borderBoxPr", "sPrePr", "oMathParaPr"):
            return ""
        # transparent containers
        if name in ("e", "num", "den", "sub", "sup", "lim", "deg", "fName", "oMath", "box", "phant",
                    "r_", "mr", "mc"):
            return self.seq(el)
        self.unsupported.add(name)
        return self.seq(el)

    # -- leaf / structure nodes -------------------------------------------
    def _n_r(self, el):
        return self._run(el)

    def _n_f(self, el):
        pr = _child(el, "fPr")
        kind = _val(_child(pr, "type"), "bar") if pr is not None else "bar"
        num, den = self.arg(_child(el, "num")), self.arg(_child(el, "den"))
        if kind == "noBar":
            return rf"\binom{{{num}}}{{{den}}}"
        if kind == "lin":
            return f"{{{num}}}/{{{den}}}"
        return rf"\frac{{{num}}}{{{den}}}"

    def _n_sSup(self, el):
        return f"{{{self.arg(_child(el, 'e'))}}}^{{{self.arg(_child(el, 'sup'))}}}"

    def _n_sSub(self, el):
        return f"{{{self.arg(_child(el, 'e'))}}}_{{{self.arg(_child(el, 'sub'))}}}"

    def _n_sSubSup(self, el):
        return (f"{{{self.arg(_child(el, 'e'))}}}_{{{self.arg(_child(el, 'sub'))}}}"
                f"^{{{self.arg(_child(el, 'sup'))}}}")

    def _n_sPre(self, el):
        return (f"{{}}_{{{self.arg(_child(el, 'sub'))}}}^{{{self.arg(_child(el, 'sup'))}}}"
                f"{{{self.arg(_child(el, 'e'))}}}")

    def _n_rad(self, el):
        pr = _child(el, "radPr")
        hide = _val(_child(pr, "degHide")) in ("1", "on", "true") if pr is not None else False
        deg = self.arg(_child(el, "deg"))
        body = self.arg(_child(el, "e"))
        return rf"\sqrt{{{body}}}" if hide or not deg else rf"\sqrt[{deg}]{{{body}}}"

    def _n_nary(self, el):
        pr = _child(el, "naryPr")
        chr_ = _val(_child(pr, "chr"), "∫") if pr is not None else "∫"
        sub_hide = _val(_child(pr, "subHide")) in ("1", "on", "true") if pr is not None else False
        sup_hide = _val(_child(pr, "supHide")) in ("1", "on", "true") if pr is not None else False
        op = _NARY.get(chr_)
        if op is None:
            self.unsupported.add(f"nary:{chr_}")
            op = r"\sum"
        sub = "" if sub_hide else self.arg(_child(el, "sub"))
        sup = "" if sup_hide else self.arg(_child(el, "sup"))
        out = op
        if sub:
            out += f"_{{{sub}}}"
        if sup:
            out += f"^{{{sup}}}"
        return out + " " + self.arg(_child(el, "e"))

    def _n_d(self, el):
        pr = _child(el, "dPr")
        beg = _val(_child(pr, "begChr"), "(") if pr is not None else "("
        end = _val(_child(pr, "endChr"), ")") if pr is not None else ")"
        sep = _val(_child(pr, "sepChr"), "|") if pr is not None else "|"
        parts = [self.arg(e) for e in _children(el, "e")]
        sep_l = _DELIMS.get(sep, sep)
        inner = f" {sep_l} ".join(parts)
        return rf"\left{_DELIMS.get(beg, beg)} {inner} \right{_DELIMS.get(end, end)}"

    def _n_func(self, el):
        name = self.arg(_child(el, "fName")).replace(r"\mathrm{", "").replace("}", "").strip()
        body = self.arg(_child(el, "e"))
        if name in _FUNCS:
            return rf"\{name} {body}"
        return rf"\operatorname{{{name}}} {body}"

    def _n_limLow(self, el):
        base = self.arg(_child(el, "e"))
        lim = self.arg(_child(el, "lim"))
        bare = base.replace(r"\mathrm{", "").replace("}", "").strip()
        if bare in _FUNCS:
            return rf"\{bare}_{{{lim}}}"
        return rf"\underset{{{lim}}}{{{base}}}"

    def _n_limUpp(self, el):
        return rf"\overset{{{self.arg(_child(el, 'lim'))}}}{{{self.arg(_child(el, 'e'))}}}"

    def _n_acc(self, el):
        pr = _child(el, "accPr")
        chr_ = _val(_child(pr, "chr"), "̂") if pr is not None else "̂"
        cmd = _ACCENTS.get(chr_)
        if cmd is None:
            self.unsupported.add(f"accent:{chr_}")
            cmd = r"\hat"
        return rf"{cmd}{{{self.arg(_child(el, 'e'))}}}"

    def _n_bar(self, el):
        pr = _child(el, "barPr")
        pos = _val(_child(pr, "pos"), "top") if pr is not None else "top"
        cmd = r"\underline" if pos == "bot" else r"\overline"
        return rf"{cmd}{{{self.arg(_child(el, 'e'))}}}"

    def _n_groupChr(self, el):
        pr = _child(el, "groupChrPr")
        pos = _val(_child(pr, "pos"), "bot") if pr is not None else "bot"
        cmd = r"\overbrace" if pos == "top" else r"\underbrace"
        return rf"{cmd}{{{self.arg(_child(el, 'e'))}}}"

    def _n_borderBox(self, el):
        return rf"\boxed{{{self.arg(_child(el, 'e'))}}}"

    def _n_m(self, el):
        rows = []
        for mr in _children(el, "mr"):
            rows.append(" & ".join(self.arg(e) for e in _children(mr, "e")))
        return r"\begin{matrix} " + r" \\ ".join(rows) + r" \end{matrix}"

    def _n_eqArr(self, el):
        rows = [self.arg(e) for e in _children(el, "e")]
        return r"\begin{aligned} " + r" \\ ".join(rows) + r" \end{aligned}"


def omml_to_latex(el: Element) -> Converted:
    """Convert an <m:oMath> or <m:oMathPara> element."""
    conv = _Converter()
    name = _t(el)
    if name == "oMathPara":
        parts = [conv.seq(m).strip() for m in _children(el, "oMath")]
        latex = r" \\ ".join(p for p in parts if p)
        display = True
    else:
        latex = conv.seq(el).strip()
        display = False
    latex = " ".join(latex.split())
    return Converted(latex=latex, display=display, unsupported=conv.unsupported)
