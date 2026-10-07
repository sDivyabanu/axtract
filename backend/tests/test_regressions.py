"""Regression tests: one per bug per format.

Bug 1  preview must render for every format (and never crash a parse when it cannot)
Bug 2  chart values must be extracted from PDF, DOCX, PPTX and images
Bug 3  equations must be detected and converted to LaTeX from every format

All inputs are generated in code (tests/fixtures/builders.py); nothing depends on sample_files/.
Tests that need LibreOffice or the formula weights skip when those are not installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402
from services import preview_service  # noqa: E402
from services.vision import models  # noqa: E402
from services.vision.latex import validate_latex  # noqa: E402
from tests.fixtures import builders as fx  # noqa: E402

needs_office = pytest.mark.skipif(preview_service.find_soffice() is None, reason="LibreOffice not installed")
needs_formula_model = pytest.mark.skipif(not models.latex_model_available(), reason="formula weights not fetched")

MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def work(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("fixtures")


def parse(client: TestClient, path: Path) -> dict:
    ext = path.suffix.lstrip(".")
    resp = client.post("/api/parse", files={"file": (path.name, path.read_bytes(), MIME[ext])})
    assert resp.status_code == 200, resp.text
    return resp.json()


def blocks_of(doc: dict, type_: str) -> list[dict]:
    return [b for b in doc["blocks"] if b["type"] == type_]


def series_values(chart: dict) -> list[list[float | None]]:
    return [s["values"] for s in chart["metadata"]["chart_data"]["series"]]


def assert_close(actual, expected, tol):
    assert len(actual) == len(expected), (actual, expected)
    for a, e in zip(actual, expected):
        assert a is not None and abs(a - e) <= tol, (actual, expected)


# ===========================================================================
# Bug 1: preview
# ===========================================================================


class TestPreview:
    def test_preview_router_is_mounted(self, client):
        # it used to be a 404 for every file because the router was never included
        assert client.post("/api/preview").status_code != 404

    def test_pdf_preview(self, client, work):
        pdf = fx.pdf_with_paragraphs(work / "p.pdf", ["Hello preview."])
        doc = parse(client, pdf)
        assert doc["preview_available"] and doc["preview_pages"] == 1 and doc["preview_error"] is None
        r = client.get(f"/api/preview/{doc['document_id']}/pages/1")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_image_preview(self, client, work):
        png = fx.text_png(work / "t.png")
        doc = parse(client, png)
        assert doc["preview_available"]
        assert client.get(f"/api/preview/{doc['document_id']}/pages/1").status_code == 200

    def test_stateless_preview_endpoint(self, client, work):
        pdf = fx.pdf_with_paragraphs(work / "s.pdf", ["Stateless."])
        r = client.post("/api/preview?page=1", files={"file": ("s.pdf", pdf.read_bytes(), MIME["pdf"])})
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"

    def test_page_out_of_range_is_a_structured_error(self, client, work):
        doc = parse(client, fx.pdf_with_paragraphs(work / "o.pdf", ["x"]))
        r = client.get(f"/api/preview/{doc['document_id']}/pages/9")
        assert r.status_code == 404 and r.json()["error"]["code"] == "PREVIEW_UNAVAILABLE"

    def test_unknown_document_id_is_rejected(self, client):
        r = client.get("/api/preview/../../etc/passwd/pages/1")
        assert r.status_code in (404, 422)
        r = client.get("/api/preview/" + "0" * 32 + "/pages/1")
        assert r.status_code == 404

    @needs_office
    @pytest.mark.parametrize("kind", ["docx", "pptx", "xlsx"])
    def test_office_preview(self, client, work, kind):
        path = {
            "docx": lambda: fx.docx_with_picture(work / "o.docx", fx.bar_chart_png(work / "c.png")),
            "pptx": lambda: fx.pptx_with_picture(work / "o.pptx", fx.bar_chart_png(work / "c2.png")),
            "xlsx": lambda: fx.xlsx_with_merges(work / "o.xlsx"),
        }[kind]()
        doc = parse(client, path)
        assert doc["preview_available"], doc["preview_error"]
        r = client.get(f"/api/preview/{doc['document_id']}/pages/1")
        assert r.status_code == 200 and r.content[:4] == b"\x89PNG"

    @needs_office
    def test_docx_blocks_get_preview_boxes(self, client, work):
        doc = parse(client, fx.docx_with_picture(work / "box.docx", fx.bar_chart_png(work / "c3.png"), "Caption line"))
        located = [b for b in doc["blocks"] if (b["metadata"].get("preview") or {}).get("bbox")]
        assert located, "no DOCX block was located in the preview"
        for b in located:
            x0, y0, x1, y1 = b["metadata"]["preview"]["bbox"]
            assert 0 <= x0 < x1 <= 1.001 and 0 <= y0 < y1 <= 1.001

    def test_preview_failure_never_fails_the_parse(self, client, work, monkeypatch):
        monkeypatch.setattr(preview_service, "find_soffice", lambda: None)
        doc = parse(client, fx.docx_with_picture(work / "np.docx", fx.bar_chart_png(work / "c4.png")))
        assert doc["status"] in ("success", "partial")
        assert doc["preview_available"] is False
        assert "LibreOffice" in doc["preview_error"]
        assert doc["blocks"], "parse result must still be returned"


# ===========================================================================
# Bug 2: charts
# ===========================================================================


class TestCharts:
    def test_pdf_vector_chart_values_are_exact(self, client, work):
        doc = parse(client, fx.vector_chart_pdf(work / "vec.pdf"))
        charts = blocks_of(doc, "chart")
        by_type = {c["metadata"]["chart_type"]: c for c in charts}
        assert {"bar", "line", "scatter"} <= set(by_type)
        assert_close(series_values(by_type["bar"])[0], fx.BAR_VALUES, 1e-3)
        assert_close(series_values(by_type["line"])[0], fx.LINE_Y, 1e-3)
        assert_close(series_values(by_type["scatter"])[0], fx.SCATTER_Y, 1e-3)
        assert by_type["bar"]["extractor"] == "pdf_vector_chart"
        assert by_type["bar"]["metadata"]["values_estimated"] is False

    def test_pdf_embedded_picture_chart(self, client, work):
        """Used to produce no figure block at all (image blocks were dropped)."""
        pdf = fx.pdf_with_images(work / "pic.pdf", [(fx.bar_chart_png(work / "b.png"), "Figure 1: Revenue")])
        doc = parse(client, pdf)
        charts = blocks_of(doc, "chart")
        assert len(charts) == 1
        assert_close(series_values(charts[0])[0], fx.BAR_VALUES, 0.6)
        assert charts[0]["extractor"] == "raster_chart_cv"
        assert charts[0]["bbox"] is not None

    def test_pdf_figure_that_is_not_a_chart_stays_a_figure(self, client, work):
        pdf = fx.pdf_with_images(work / "nf.pdf", [(fx.text_png(work / "nf.png"), "Figure 1")])
        doc = parse(client, pdf)
        assert not blocks_of(doc, "chart")
        assert blocks_of(doc, "figure"), "image blocks must be extracted from PDFs"

    def test_docx_native_chart_is_exact(self, client, work):
        doc = parse(client, fx.docx_with_native_chart(work / "native.docx"))
        charts = blocks_of(doc, "chart")
        assert len(charts) == 1
        c = charts[0]
        assert c["extractor"] == "office_chart_xml"
        assert c["metadata"]["chart_data"]["categories"] == fx.BAR_LABELS
        assert series_values(c)[0] == [float(v) for v in fx.BAR_VALUES]
        assert c["metadata"]["values_estimated"] is False and c["confidence"] >= 0.9

    def test_pptx_native_chart_is_exact(self, client, work):
        doc = parse(client, fx.pptx_with_native_chart(work / "native.pptx"))
        charts = blocks_of(doc, "chart")
        assert len(charts) == 1
        assert charts[0]["extractor"] == "office_chart_xml"
        assert series_values(charts[0])[0] == [float(v) for v in fx.BAR_VALUES]
        assert charts[0]["bbox"] is not None

    def test_docx_picture_chart(self, client, work):
        doc = parse(client, fx.docx_with_picture(work / "pc.docx", fx.bar_chart_png(work / "pc.png")))
        charts = blocks_of(doc, "chart")
        assert len(charts) == 1
        assert_close(series_values(charts[0])[0], fx.BAR_VALUES, 0.6)

    def test_pptx_picture_chart(self, client, work):
        doc = parse(client, fx.pptx_with_picture(work / "pc.pptx", fx.line_chart_png(work / "lc.png")))
        charts = blocks_of(doc, "chart")
        assert len(charts) == 1 and charts[0]["metadata"]["chart_type"] == "line"
        assert_close(series_values(charts[0])[0], fx.LINE_Y, 0.05)

    @pytest.mark.parametrize("ext", ["png", "jpg"])
    def test_image_chart(self, client, work, ext):
        src = fx.bar_chart_png(work / f"img_{ext}.png")
        path = src
        if ext == "jpg":
            from PIL import Image

            path = work / "img.jpg"
            Image.open(src).convert("RGB").save(path, quality=90)
        doc = parse(client, path)
        charts = blocks_of(doc, "chart")
        assert len(charts) == 1
        assert_close(series_values(charts[0])[0], fx.BAR_VALUES, 0.6)
        assert charts[0]["metadata"]["chart_data"]["categories"] == fx.BAR_LABELS

    def test_estimated_values_are_flagged(self, client, work):
        doc = parse(client, fx.line_chart_png(work / "est.png"))
        chart = blocks_of(doc, "chart")[0]
        assert chart["metadata"]["values_estimated"] is True

    def test_unreadable_chart_is_not_invented(self, client, work):
        # a grey-scale photo-like image: must stay a figure/paragraphs, never a made-up chart
        from PIL import Image
        import numpy as np

        rng = np.random.default_rng(0)
        Image.fromarray((rng.random((300, 400, 3)) * 255).astype("uint8")).save(work / "noise.png")
        doc = parse(client, work / "noise.png")
        assert not blocks_of(doc, "chart")

    def test_chart_markdown_contains_the_data(self, client, work):
        doc = parse(client, fx.docx_with_native_chart(work / "md.docx"))
        md = doc["markdown"]
        assert "Q1" in md and "190" in md and "| " in md


# ===========================================================================
# Bug 3: equations
# ===========================================================================


class TestEquations:
    def test_docx_omml_is_converted_without_a_model(self, client, work):
        doc = parse(client, fx.docx_with_omml(work / "eq.docx"))
        eqs = blocks_of(doc, "equation")
        assert len(eqs) == 1
        e = eqs[0]
        assert e["metadata"]["latex"] == fx.OMML_EXPECTED
        assert e["metadata"]["latex_method"] == "omml" and e["metadata"]["latex_validated"] is True
        assert e["confidence"] >= 0.9 and not e["requires_review"]
        # position in reading order: between the two paragraphs
        order = [b["content"] for b in sorted(doc["blocks"], key=lambda b: b["reading_order"])]
        assert order.index("The quadratic formula follows.") < order.index(fx.OMML_EXPECTED) < order.index("End of math.")

    def test_pptx_omml_is_converted_and_the_fallback_picture_text_is_ignored(self, client, work):
        doc = parse(client, fx.pptx_with_omml(work / "eq.pptx"))
        eqs = blocks_of(doc, "equation")
        assert len(eqs) == 1 and eqs[0]["metadata"]["latex"] == fx.OMML_EXPECTED
        assert eqs[0]["bbox"] is not None
        assert not any("fallback picture" in b["content"] and b["type"] == "equation" for b in doc["blocks"])

    @needs_formula_model
    def test_pdf_equation_image(self, client, work):
        png = fx.equation_png(work / "e1.png", r"$E = mc^{2}$")
        doc = parse(client, fx.pdf_with_images(work / "eq.pdf", [(png, "Equation 1")]))
        eqs = blocks_of(doc, "equation")
        assert len(eqs) == 1
        e = eqs[0]
        assert validate_latex(e["metadata"]["latex"]).ok
        assert "mc" in e["metadata"]["latex"].replace(" ", "")
        assert e["bbox"] is not None and e["confidence"] is not None
        assert e["metadata"]["latex_method"] == "pix2tex_onnx"

    @needs_formula_model
    def test_png_equation_image(self, client, work):
        doc = parse(client, fx.equation_png(work / "only.png", r"$x = \frac{a}{b}$"))
        eqs = blocks_of(doc, "equation")
        assert len(eqs) == 1 and validate_latex(eqs[0]["metadata"]["latex"]).ok
        assert "\\frac" in eqs[0]["metadata"]["latex"]

    @needs_formula_model
    def test_docx_equation_picture_with_caption(self, client, work):
        png = fx.equation_png(work / "e3.png", r"$E = mc^{2}$")
        doc = parse(client, fx.docx_with_picture(work / "eqpic.docx", png, "Equation 1"))
        eqs = blocks_of(doc, "equation")
        assert len(eqs) == 1 and validate_latex(eqs[0]["metadata"]["latex"]).ok

    def test_pdf_inline_text_equation(self, client, work):
        pdf = fx.pdf_with_paragraphs(work / "inl.pdf", ["The circle satisfies x^2 + y^2 = r^2 in the plane."])
        doc = parse(client, pdf)
        eqs = blocks_of(doc, "equation")
        assert [e["metadata"]["latex"] for e in eqs] == ["x^{2} + y^{2} = r^{2}"]
        assert eqs[0]["metadata"]["inline"] is True and eqs[0]["bbox"] is not None
        assert blocks_of(doc, "paragraph"), "the sentence itself stays a paragraph"

    def test_hyphenated_and_dated_text_is_not_an_equation(self, client, work):
        """The old heuristic typed any text with two hyphens or slashes as an equation."""
        pdf = fx.pdf_with_paragraphs(work / "fp.pdf", [
            "This state-of-the-art, well-known method was published on 2024-01-05 and/or later.",
            "Call 555-123-4567 or write to the e-mail address on file.",
        ])
        doc = parse(client, pdf)
        assert not blocks_of(doc, "equation")

    @needs_formula_model
    def test_plain_text_image_is_not_an_equation(self, client, work):
        doc = parse(client, fx.text_png(work / "txt.png"))
        assert not blocks_of(doc, "equation")
        assert blocks_of(doc, "paragraph")

    @needs_formula_model
    def test_unreliable_formula_is_flagged_not_trusted(self, client, work):
        png = fx.equation_png(work / "hard.png", r"$\int_0^\infty e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}$", size=(6, 1.8))
        doc = parse(client, fx.pdf_with_images(work / "hard.pdf", [(png, "Equation 2")]))
        eqs = blocks_of(doc, "equation")
        assert len(eqs) == 1
        e = eqs[0]
        # whatever the model returned, a result that is wrong or unverified must carry a flag
        if e["confidence"] is not None and e["confidence"] < 0.7:
            assert e["requires_review"] and e["metadata"]["flags"]
        assert e["confidence"] <= 0.75, "model output must never be reported with high confidence"


# ===========================================================================
# Review fixes that the above depends on
# ===========================================================================


class TestExtractionFixes:
    def test_pdf_lines_are_joined_with_spaces(self, client, work):
        text = ("This report presents the consolidated revenue detail for the holding company and its "
                "subsidiaries across every region where the group operates throughout the fiscal year.")
        doc = parse(client, fx.pdf_with_paragraphs(work / "wrap.pdf", [text]))
        assert text in doc["blocks"][0]["content"] or text.split()[-1] in doc["blocks"][0]["content"]
        assert "sothat" not in doc["markdown"] and "  " not in doc["blocks"][0]["content"]
        words = doc["blocks"][0]["content"].split()
        assert len(words) == len(text.split())

    def test_docx_keeps_document_order(self, client, work):
        import docx

        d = docx.Document()
        d.add_paragraph("before the table")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text = "H1"
        d.add_paragraph("after the table")
        path = work / "order.docx"
        d.save(path)
        doc = parse(client, path)
        types = [b["type"] for b in sorted(doc["blocks"], key=lambda b: b["reading_order"])]
        assert types == ["paragraph", "table", "paragraph"]

    def test_docx_merged_cells(self, client, work):
        import docx

        d = docx.Document()
        t = d.add_table(rows=2, cols=3)
        t.cell(0, 0).merge(t.cell(0, 2)).text = "Merged title"
        for i, h in enumerate(["a", "b", "c"]):
            t.cell(1, i).text = h
        path = work / "merge.docx"
        d.save(path)
        table = blocks_of(parse(client, path), "table")[0]
        assert table["metadata"]["merged_cells"]["merged_regions"] == [[0, 0, 1, 3]]
        assert table["metadata"]["rows"][0] == ["Merged title", None, None]

    def test_xlsx_merged_cells(self, client, work):
        table = blocks_of(parse(client, fx.xlsx_with_merges(work / "m.xlsx")), "table")[0]
        assert table["metadata"]["merged_cells"]["merged_regions"] == [[0, 0, 1, 3]]

    def test_upload_over_limit_is_rejected_while_streaming(self, client, monkeypatch):
        from utils import files

        monkeypatch.setattr(files, "MAX_UPLOAD_BYTES", 1024)
        r = client.post("/api/parse", files={"file": ("big.pdf", b"%PDF-1.4 " + b"x" * 5000, "application/pdf")})
        assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"

    def test_truncated_pdf_is_an_error_not_success(self, client):
        r = client.post("/api/parse", files={"file": ("t.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj", "application/pdf")})
        assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_FILE"

    def test_errors_keep_cors_headers(self, client):
        r = client.post("/api/parse", files={"file": ("x.txt", b"hello", "text/plain")},
                        headers={"Origin": "http://localhost:3000"})
        assert r.status_code == 415
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"
