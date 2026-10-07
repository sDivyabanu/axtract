"""Unit tests for the pure-logic pieces behind the regression tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models.document import BlockType, DocumentBlock  # noqa: E402
from models.errors import AppError  # noqa: E402
from services.table_service import (  # noqa: E402
    detect_merged_cells,
    enhance_table_block,
    merge_cross_page_tables,
    parse_financial_number,
)
from services.vision.chart_native import parse_chart_xml  # noqa: E402
from services.vision.latex import (  # noqa: E402
    crosscheck,
    find_inline_equations,
    plain_to_latex,
    validate_latex,
)
from services.vision.omml import omml_to_latex  # noqa: E402
from utils import deadline  # noqa: E402

M = 'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"'


def r(t: str) -> str:
    return f"<m:r><m:t>{t}</m:t></m:r>"


class TestValidateLatex:
    @pytest.mark.parametrize("s", [
        r"E=mc^{2}", r"\frac{a}{b}", r"\sum_{n=1}^{\infty}\frac{1}{n^2}", r"\left( x \right)",
        r"\begin{matrix} a & b \\ c & d \end{matrix}", r"\sqrt[3]{x}",
    ])
    def test_valid(self, s):
        assert validate_latex(s).ok, validate_latex(s).errors

    @pytest.mark.parametrize("s", [
        "", r"\frac{a}{", r"x^{2", r"}{", r"\foo{3}", r"\left( x", r"x^", r"x^{}", r"\begin{matrix} a",
        r"\begin{matrix} a \end{pmatrix}", r"a^^b",
    ])
    def test_invalid(self, s):
        assert not validate_latex(s).ok


class TestOmml:
    def test_fraction_scripts_radical(self):
        el = fromstring(
            f'<m:oMath {M}><m:f><m:num>{r("a")}</m:num><m:den><m:sSup><m:e>{r("b")}</m:e><m:sup>{r("2")}</m:sup></m:sSup></m:den></m:f></m:oMath>'
        )
        c = omml_to_latex(el)
        assert c.latex == r"\frac{a}{{b}^{2}}" and not c.display and not c.unsupported

    def test_display_paragraph(self):
        el = fromstring(f"<m:oMathPara {M}><m:oMath>{r('x')}</m:oMath><m:oMath>{r('y')}</m:oMath></m:oMathPara>")
        c = omml_to_latex(el)
        assert c.display and c.latex == r"x \\ y"

    def test_nary_and_function_and_delimiter(self):
        el = fromstring(
            f'<m:oMath {M}><m:nary><m:naryPr><m:chr m:val="∫"/></m:naryPr><m:sub>{r("0")}</m:sub><m:sup>{r("1")}</m:sup>'
            f'<m:e><m:func><m:fName><m:r><m:rPr><m:sty m:val="p"/></m:rPr><m:t>sin</m:t></m:r></m:fName><m:e>{r("x")}</m:e></m:func></m:e></m:nary>'
            f'<m:d><m:e>{r("a")}</m:e><m:e>{r("b")}</m:e></m:d></m:oMath>'
        )
        c = omml_to_latex(el)
        assert r"\int_{0}^{1}" in c.latex and r"\sin x" in c.latex and r"\left(" in c.latex
        assert validate_latex(c.latex).ok

    def test_unknown_constructs_are_reported_not_hidden(self):
        c = omml_to_latex(fromstring(f"<m:oMath {M}><m:blorp>{r('z')}</m:blorp></m:oMath>"))
        assert c.unsupported == {"blorp"} and c.latex == "z"

    def test_greek_and_operators(self):
        c = omml_to_latex(fromstring(f"<m:oMath {M}>{r('α≤β')}</m:oMath>"))
        assert c.latex == r"\alpha \leq \beta"


class TestInlineEquations:
    def test_finds_math_in_a_sentence(self):
        found = find_inline_equations("The circle satisfies x^2 + y^2 = r^2 in the plane.")
        assert found == ["x^2 + y^2 = r^2"]
        assert plain_to_latex(found[0]) == "x^{2} + y^{2} = r^{2}"

    @pytest.mark.parametrize("text", [
        "state-of-the-art, well-known and/or e-mail", "Due 2024-01-05 or call 555-1234",
        "Revenue grew 10 percent year-over-year", "Let y be the answer",
    ])
    def test_prose_is_not_math(self, text):
        assert find_inline_equations(text) == []

    def test_crosscheck(self):
        assert crosscheck(r"E=mc^{2}", "E = mc²") == 1.0
        assert crosscheck(r"\frac{a}{b}", "hello world") == 0.0


class TestNativeChartXml:
    def test_missing_points_stay_none(self):
        xml = (
            b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:plotArea>'
            b'<c:lineChart><c:ser><c:cat><c:strLit><c:ptCount val="3"/><c:pt idx="0"><c:v>A</c:v></c:pt>'
            b'<c:pt idx="1"><c:v>B</c:v></c:pt><c:pt idx="2"><c:v>C</c:v></c:pt></c:strLit></c:cat>'
            b'<c:val><c:numLit><c:ptCount val="3"/><c:pt idx="0"><c:v>1.5</c:v></c:pt><c:pt idx="2"><c:v>3</c:v></c:pt></c:numLit></c:val>'
            b"</c:ser></c:lineChart></c:plotArea></c:chart></c:chartSpace>"
        )
        d = parse_chart_xml(xml)
        assert d["chart_type"] == "line" and d["categories"] == ["A", "B", "C"]
        assert d["series"][0]["values"] == [1.5, None, 3.0]

    def test_no_series_returns_none(self):
        xml = b'<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:plotArea/></c:chart></c:chartSpace>'
        assert parse_chart_xml(xml) is None

    def test_doctype_is_rejected(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"/>'
        with pytest.raises(Exception):
            parse_chart_xml(evil)


class TestNumberParsing:
    @pytest.mark.parametrize("raw,expected", [
        ("(1,234)", -1234), ("1,234.56", 1234.56), ("$1,234", 1234), ("50%", 0.5), ("($2,500.75)", -2500.75),
        ("42", 42), ("12.5%", 0.125), ("(12.5%)", -0.125), ("-222.1%", -2.221), ("1.234,56", 1234.56), ("1,23", 1.23),  # 2 digits after a comma cannot be thousands

    ])
    def test_numbers(self, raw, expected):
        assert parse_financial_number(raw) == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["N/A", "-", "NaN", "nan", "Nan", "inf", "Infinity", "1_000", "1e5", "1,2,3", "abc", ""])
    def test_not_numbers_are_returned_unchanged(self, raw):
        assert parse_financial_number(raw) == raw


class TestTables:
    def test_merged_cells_from_none_continuations(self):
        rows = [["Segment", "FY24", None, "FY23", None], [None, "H1", "H2", "H1", "H2"], ["A", "1", "2", "3", "4"],
                ["Note", None, None, None, None]]
        regions = detect_merged_cells(rows)["merged_regions"]
        assert (0, 0, 2, 1) in regions and (0, 1, 1, 2) in regions and (0, 3, 1, 2) in regions and (3, 0, 1, 5) in regions
        assert not any(rg[0] == 2 for rg in regions)

    def _table(self, bid, page, rows):
        return DocumentBlock(
            id=bid, type=BlockType.TABLE, content="\n".join(" | ".join(r) for r in rows), page=page,
            bbox=(0.1, 0.1, 0.9, 0.9), extractor="t", metadata={"rows": rows, "col_count": len(rows[0])},
        )

    def test_three_page_chain_with_repeated_headers(self):
        h = ["Item", "Value"]
        blocks = [self._table("a", 1, [h, ["x", "1"]]), self._table("b", 2, [h, ["y", "2"]]),
                  self._table("c", 3, [h, ["z", "3"]])]
        blocks = [enhance_table_block(b) for b in blocks]
        out = merge_cross_page_tables(blocks)
        assert len(out) == 1
        assert out[0].metadata["rows"] == [h, ["x", "1"], ["y", "2"], ["z", "3"]]
        assert out[0].metadata["merged_from_pages"] == [1, 2, 3]
        assert len(out[0].metadata["parsed_rows"]) == 4  # recomputed after the merge

    def test_first_row_is_kept_when_it_is_not_a_repeated_header(self):
        blocks = [enhance_table_block(self._table("a", 1, [["Item", "Value"], ["x", "1"]])),
                  enhance_table_block(self._table("b", 2, [["y", "2"], ["z", "3"]]))]
        out = merge_cross_page_tables(blocks)
        assert out[0].metadata["rows"] == [["Item", "Value"], ["x", "1"], ["y", "2"], ["z", "3"]]

    def test_order_of_other_blocks_is_preserved(self):
        p = lambda i: DocumentBlock(id=f"p{i}", type=BlockType.PARAGRAPH, content="t", page=1, extractor="t")
        t = enhance_table_block(self._table("t", 1, [["a", "b"], ["1", "2"]]))
        out = merge_cross_page_tables([p(0), t, p(1)])
        assert [b.id for b in out] == ["p0", "t", "p1"]


class TestDeadline:
    def test_check_raises_a_structured_timeout(self):
        deadline.start(-1)
        with pytest.raises(AppError) as exc:
            deadline.check()
        assert exc.value.code == "TIMEOUT" and exc.value.status_code == 504
        deadline.start(60)
        deadline.check()
