# tests/test_pdf_guard.py
"""
Tests for pdf_guard.py
"""
import sys, os, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from pdf_guard import scan_pdf_bytes


# ── PDF builder ───────────────────────────────────────────────────────────────

def _make_base_pdf() -> bytes:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 700, "Revenue: Rs 500 Cr | EBITDA: Rs 120 Cr")
    c.save()
    return buf.getvalue()


def _inject_catalog(catalog_keys: dict) -> bytes:
    """Inject keys into the PDF catalog (not trailer)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    doc = fitz.open(stream=_make_base_pdf(), filetype="pdf")

    # get catalog xref
    root_ref  = doc.xref_get_key(-1, "Root")[1]
    cat_xref  = int(root_ref.split()[0])

    for key, val in catalog_keys.items():
        doc.xref_set_key(cat_xref, key, val)

    out = io.BytesIO()
    doc.save(out)
    doc.close()
    return out.getvalue()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_clean_pdf():
    result = scan_pdf_bytes(_make_base_pdf())
    assert result["clean"] is True
    assert result["findings"] == []
    print("  PASS  test_clean_pdf")


def test_javascript_detected():
    data = _inject_catalog({
        "OpenAction": "<< /S /JavaScript /JS (app.alert\\(1\\);) >>"
    })
    result = scan_pdf_bytes(data)
    types  = [f["type"] for f in result["findings"]]
    assert "pdf_javascript" in types or "pdf_auto_action" in types, \
        f"expected js/autoaction, got {types}"
    assert result["clean"] is False
    print("  PASS  test_javascript_detected")


def test_openaction_detected():
    data = _inject_catalog({
        "OpenAction": "<< /S /JavaScript /JS (x=1;) >>"
    })
    result = scan_pdf_bytes(data)
    types  = [f["type"] for f in result["findings"]]
    assert "pdf_auto_action" in types or "pdf_javascript" in types, \
        f"expected auto_action, got {types}"
    print("  PASS  test_openaction_detected")


def test_launch_detected():
    data = _inject_catalog({
        "OpenAction": "<< /S /Launch /F (evil.exe) >>"
    })
    result = scan_pdf_bytes(data)
    assert len(result["findings"]) > 0, \
        f"expected findings for Launch, got {result['findings']}"
    print("  PASS  test_launch_detected")


def test_embedded_files_detected():
    data = _inject_catalog({
        "Names": "<< /EmbeddedFiles << /Names [] >> >>"
    })
    result = scan_pdf_bytes(data)
    types  = [f["type"] for f in result["findings"]]
    assert "pdf_embedded_file" in types, \
        f"expected embedded_file, got {types}"
    print("  PASS  test_embedded_files_detected")


def test_action_taken_never_executed():
    data = _inject_catalog({
        "OpenAction": "<< /S /JavaScript /JS (x=1;) >>"
    })
    result = scan_pdf_bytes(data)
    for f in result["findings"]:
        assert f["action_taken"] == "not_executed"
    print("  PASS  test_action_taken_never_executed")


def test_multiple_findings():
    data = _inject_catalog({
        "OpenAction": "<< /S /JavaScript /JS (x=1;) >>",
        "Names":      "<< /EmbeddedFiles << /Names [] >> >>",
    })
    result = scan_pdf_bytes(data)
    assert len(result["findings"]) >= 2, \
        f"expected >=2 findings, got {len(result['findings'])}"
    print("  PASS  test_multiple_findings")


def test_findings_have_required_fields():
    data = _inject_catalog({
        "OpenAction": "<< /S /JavaScript /JS (x=1;) >>"
    })
    result = scan_pdf_bytes(data)
    for f in result["findings"]:
        assert "type"         in f
        assert "severity"     in f
        assert "detail"       in f
        assert "action_taken" in f
    print("  PASS  test_findings_have_required_fields")


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_clean_pdf,
        test_javascript_detected,
        test_openaction_detected,
        test_launch_detected,
        test_embedded_files_detected,
        test_action_taken_never_executed,
        test_multiple_findings,
        test_findings_have_required_fields,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}  {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")