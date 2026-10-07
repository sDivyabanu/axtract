# security/pdf_guard.py
"""
S10 — PDF active content detection.
Scans the PDF object tree for executable / auto-run content.
Nothing is ever executed — every hit becomes a security_finding.
The parse succeeds normally; caller sees what was present.

Detects:
  /JavaScript   — JS in Names tree or action dicts
  /OpenAction   — runs automatically on open
  /AA           — additional actions (page open/close etc.)
  /Launch       — opens external program or file
  /EmbeddedFiles — files embedded inside the PDF
  /SubmitForm   — form submission action
  /URI          — external URL reference (severity: low)
  /RichMedia    — Flash / video embeds
"""
try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

# ── finding types ─────────────────────────────────────────────────────────────

FINDING_MAP = {
    "JavaScript":    ("pdf_javascript",    "high"),
    "JS":            ("pdf_javascript",    "high"),
    "OpenAction":    ("pdf_auto_action",   "high"),
    "AA":            ("pdf_auto_action",   "medium"),
    "Launch":        ("pdf_launch_action", "high"),
    "EmbeddedFiles": ("pdf_embedded_file", "medium"),
    "SubmitForm":    ("pdf_submit_form",   "medium"),
    "RichMedia":     ("pdf_rich_media",    "low"),
    "URI":           ("pdf_uri",           "low"),
}


def _make_finding(key: str, detail: str,
                  location: dict = None) -> dict:
    ftype, severity = FINDING_MAP.get(key, ("pdf_active_content", "medium"))
    return {
        "type":         ftype,
        "severity":     severity,
        "detail":       detail,
        "location":     location or {},
        "action_taken": "not_executed",
    }


# ── catalog scanner ───────────────────────────────────────────────────────────

# REPLACE _scan_catalog in security/pdf_guard.py

def _scan_catalog(doc) -> list:
    findings = []
    try:
        # -1 is the TRAILER not the catalog
        # get the actual catalog xref via Root reference
        root_ref  = doc.xref_get_key(-1, "Root")[1]   # e.g. "4 0 R"
        cat_xref  = int(root_ref.split()[0])           # e.g. 4
        obj_str   = doc.xref_object(cat_xref, compressed=False)

        for key in FINDING_MAP:
            if f"/{key}" in obj_str:
                findings.append(_make_finding(
                    key,
                    f"/{key} found in document catalog",
                    location={"path": f"Catalog/{key}"}))
    except Exception:
        pass
    return findings


# ── xref raw scanner ──────────────────────────────────────────────────────────

def _scan_xref_raw(doc) -> list:
    findings = []
    seen = set()
    for xref in range(1, doc.xref_length()):
        try:
            obj = doc.xref_object(xref, compressed=False) or ""
            for key in FINDING_MAP:
                if f"/{key}" in obj:
                    sig = (xref, key)
                    if sig not in seen:
                        seen.add(sig)
                        findings.append(_make_finding(
                            key,
                            f"/{key} in xref {xref}",
                            location={"xref": xref}))
        except Exception:
            continue
    return findings


# ── page scanner ──────────────────────────────────────────────────────────────

def _scan_pages(doc) -> list:
    findings = []
    for i, page in enumerate(doc):
        try:
            obj_str = doc.xref_object(page.xref, compressed=False)
            if "/AA" in obj_str:
                findings.append(_make_finding(
                    "AA",
                    f"per-page /AA action on page {i+1}",
                    location={"page": i+1, "path": "Page/AA"}))
            if "/OpenAction" in obj_str:
                findings.append(_make_finding(
                    "OpenAction",
                    f"/OpenAction on page {i+1}",
                    location={"page": i+1}))
        except Exception:
            continue
    return findings


# ── public API ────────────────────────────────────────────────────────────────

def scan_pdf(path: str) -> dict:
    """
    Scan a PDF file for active content.
    Returns:
        {
          "findings": [ {type, severity, detail, location, action_taken} ],
          "clean":    bool
        }
    """
    if fitz is None:
        return {"findings": [], "clean": True,
                "error": "pymupdf not installed"}
    try:
        doc = fitz.open(path)
    except Exception as e:
        return {"findings": [], "clean": True,
                "error": f"could not open: {e}"}

    findings = []
    seen_sigs = set()

    def add(f: dict):
        sig = (f["type"], str(f.get("location", {})))
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            findings.append(f)

    for f in _scan_catalog(doc): add(f)
    for f in _scan_pages(doc):   add(f)
    for f in _scan_xref_raw(doc):add(f)

    doc.close()
    return {"findings": findings, "clean": len(findings) == 0}


def scan_pdf_bytes(data: bytes) -> dict:
    """Same as scan_pdf but from bytes."""
    if fitz is None:
        return {"findings": [], "clean": True,
                "error": "pymupdf not installed"}
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        return {"findings": [], "clean": True,
                "error": f"could not open: {e}"}

    findings = []
    seen_sigs = set()

    def add(f):
        sig = (f["type"], str(f.get("location", {})))
        if sig not in seen_sigs:
            seen_sigs.add(sig)
            findings.append(f)

    for f in _scan_catalog(doc): add(f)
    for f in _scan_pages(doc):   add(f)
    for f in _scan_xref_raw(doc):add(f)

    doc.close()
    return {"findings": findings, "clean": len(findings) == 0}