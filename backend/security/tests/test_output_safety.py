# tests/test_output_safety.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from output_safety import (
    escape_text, neutralise_uri, safe_cell_value,
    sanitise_block_text, sanitise_markdown,
    validate_response, safe_response,
)


# ── S17 XSS ──────────────────────────────────────────────────────────────────

def test_escape_normal_text():
    assert escape_text("Revenue was 500 Cr") == "Revenue was 500 Cr"


def test_escape_html_entities():
    result = escape_text("<b>bold</b> & 'quotes'")
    assert "<b>" not in result
    assert "&lt;" in result
    assert "&amp;" in result


def test_script_tag_removed():
    result = escape_text("<script>alert(1)</script>Hello")
    assert "<script>" not in result
    assert "alert" not in result
    assert "Hello" in result


def test_event_handler_removed():
    result = escape_text('<img src="x" onerror="alert(1)">')
    assert "onerror" not in result


def test_javascript_uri_neutralised():
    result = neutralise_uri("javascript:alert(1)")
    assert result.startswith("blocked:")
    assert "javascript" not in result


def test_vbscript_uri_neutralised():
    result = neutralise_uri("vbscript:msgbox(1)")
    assert result.startswith("blocked:")


def test_data_uri_neutralised():
    result = neutralise_uri("data:text/html,<script>alert(1)</script>")
    assert result.startswith("blocked:")


def test_safe_uri_unchanged():
    assert neutralise_uri("https://example.com") == "https://example.com"


def test_sanitise_block_text_xss():
    result = sanitise_block_text(
        '<script>alert("xss")</script>Normal text')
    assert "<script>" not in result
    assert "Normal text" in result


def test_sanitise_markdown_link():
    result = sanitise_markdown("[click](javascript:alert(1))")
    assert "javascript:" not in result
    assert "blocked:" in result


def test_sanitise_markdown_clean():
    md = "# Heading\n\n[link](https://example.com)"
    assert sanitise_markdown(md) == md


# ── S17 formula injection ─────────────────────────────────────────────────────

def test_formula_prefix_equals():
    assert safe_cell_value("=SUM(A1:A10)").startswith("'")


def test_formula_prefix_plus():
    assert safe_cell_value("+cmd|' /C calc'!A0").startswith("'")


def test_formula_prefix_minus():
    assert safe_cell_value("-2+3").startswith("'")


def test_formula_prefix_at():
    assert safe_cell_value("@SUM(1+1)").startswith("'")


def test_normal_cell_unchanged():
    assert safe_cell_value("500") == "500"
    assert safe_cell_value("Revenue") == "Revenue"


def test_none_cell():
    assert safe_cell_value(None) == ""


# ── S19 schema validation ─────────────────────────────────────────────────────

def test_valid_response():
    r = {"status": "success", "blocks": [
        {"id": "b1", "type": "paragraph",
         "content": "text", "confidence": 0.9}
    ]}
    valid, err = validate_response(r)
    assert valid is True
    assert err is None


def test_missing_status():
    valid, err = validate_response({"blocks": []})
    assert valid is False
    assert "status" in err


def test_missing_blocks():
    valid, err = validate_response({"status": "success"})
    assert valid is False
    assert "blocks" in err


def test_invalid_status():
    valid, err = validate_response(
        {"status": "unknown", "blocks": []})
    assert valid is False


def test_invalid_confidence():
    r = {"status": "success", "blocks": [
        {"id": "b1", "type": "paragraph", "confidence": 1.5}
    ]}
    valid, err = validate_response(r)
    assert valid is False
    assert "confidence" in err


def test_invalid_bbox():
    r = {"status": "success", "blocks": [
        {"id": "b1", "type": "paragraph",
         "bbox": [0.1, 0.2, 0.3]}      # only 3 values
    ]}
    valid, err = validate_response(r)
    assert valid is False
    assert "bbox" in err


def test_null_confidence_ok():
    r = {"status": "success", "blocks": [
        {"id": "b1", "type": "paragraph", "confidence": None}
    ]}
    valid, err = validate_response(r)
    assert valid is True


def test_safe_response_valid():
    r = {"status": "success", "blocks": []}
    out = safe_response(r)
    assert out["status"] == "success"


def test_safe_response_invalid_returns_error():
    out = safe_response({"status": "broken"})
    assert out["status"] == "error"
    assert out["error"]["code"] == "INTERNAL_VALIDATION_ERROR"
    assert out["blocks"] == []
def test_relationship_with_https_url():
    """
    Regression test:
    URLs contain '/' and must not break Relationship extraction.
    """
    from office_scan import _scan_rels_content

    content = """
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
        <Relationship
            Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://example.com/investor/reports/2026/annual-report.pdf"
            TargetMode="External"/>
    </Relationships>
    """

    result = _scan_rels_content(content)

    assert result is not None
def test_relationships_with_multiple_urls():
    from office_scan import _scan_rels_content

    content = """
    <Relationships>
        <Relationship
            Id="rId1"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://example.com/company/investor-relations"
            TargetMode="External"/>

        <Relationship
            Id="rId2"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://sec.gov/Archives/edgar/data/123456/report.htm"
            TargetMode="External"/>

        <Relationship
            Id="rId3"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
            Target="media/image1.png"/>
    </Relationships>
    """

    result = _scan_rels_content(content)

    assert result is not None
def test_relationship_url_with_fragment():
    from office_scan import _scan_rels_content

    content = """
    <Relationships>
        <Relationship
            Id="rId8"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://example.com/report/annual.pdf#page=37"
            TargetMode="External"/>
    </Relationships>
    """

    result = _scan_rels_content(content)

    assert result is not None
def test_relationship_url_with_query_string():
    from office_scan import _scan_rels_content

    content = """
    <Relationships>
        <Relationship
            Id="rId7"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://example.com/report?id=12345&amp;section=financials"
            TargetMode="External"/>
    </Relationships>
    """

    result = _scan_rels_content(content)

    assert result is not None
def test_relationship_with_many_slashes():
    from office_scan import _scan_rels_content

    content = """
    <Relationships>
        <Relationship
            Id="rId11"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://foo.com/a/b/c/d/e/f/g"
            TargetMode="External"/>
    </Relationships>
    """

    result = _scan_rels_content(content)

    assert result is not None
def test_multiple_external_relationships():
    from office_scan import _scan_rels_content

    content = """
    <Relationships>
        <Relationship
            Id="rId21"
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
            Target="https://example.com/a/b"
            TargetMode="External"/>

        <Relationship
            Id="rId22"
            Type="http://evil.example/relationship"
            Target="https://unknown.example/payload/download"
            TargetMode="External"/>
    </Relationships>
    """

    result = _scan_rels_content(content)

    assert result is not None

# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_escape_normal_text, test_escape_html_entities,
        test_script_tag_removed, test_event_handler_removed,
        test_javascript_uri_neutralised, test_vbscript_uri_neutralised,
        test_data_uri_neutralised, test_safe_uri_unchanged,
        test_sanitise_block_text_xss, test_sanitise_markdown_link,
        test_sanitise_markdown_clean,
        test_formula_prefix_equals, test_formula_prefix_plus,
        test_formula_prefix_minus, test_formula_prefix_at,
        test_normal_cell_unchanged, test_none_cell,
        test_valid_response, test_missing_status,
        test_missing_blocks, test_invalid_status,
        test_invalid_confidence, test_invalid_bbox,
        test_null_confidence_ok,
        test_safe_response_valid, test_safe_response_invalid_returns_error,
        test_multiple_external_relationships,
        test_relationship_with_many_slashes,
        test_relationship_url_with_query_string,
        test_relationship_url_with_fragment,
        test_relationship_with_https_url
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {t.__name__}  {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
# ============================================================================
# RELATIONSHIP XML TESTS
# ============================================================================

