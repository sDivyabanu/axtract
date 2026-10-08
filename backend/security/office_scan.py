# security/office_scan.py
"""
S11 — Office macro, DDE, OLE detection
F28 — Excel formula integrity / manual override detection

Covers DOCX, XLSX, PPTX (Office Open XML = zip containers).
Nothing is ever executed — every hit becomes a security_finding.

Detects:
  VBA macros     — vbaProject.bin present in the zip
  DDE fields     — DDEAUTO / DDE in field instructions (DOCX)
  OLE objects    — embeddings/ directory or oleObject parts
  Manual override— financial cells with hardcoded values, no formula (F28)
"""
import zipfile
import re
from typing import Optional
from pathlib import Path


# ── finding builder ───────────────────────────────────────────────────────────

def _finding(ftype: str, severity: str,
             detail: str, block_id: str = "") -> dict:
    return {
        "type":         ftype,
        "severity":     severity,
        "detail":       detail,
        "block_id":     block_id,
        "action_taken": "not_executed",
    }


# ── financial label detector (F28) ───────────────────────────────────────────

FINANCIAL_LABELS = re.compile(
    r'\b(total|subtotal|ebitda|revenue|sales|income|profit|loss|'
    r'net|gross|assets|liabilities|equity|cashflow|cash flow|'
    r'earnings|debt|margin|capital)\b',
    re.IGNORECASE
)


# ══════════════════════════════════════════════════════════════════════════════
# ZIP-level Office scanner (DOCX / XLSX / PPTX)
# ══════════════════════════════════════════════════════════════════════════════

def scan_office_zip(path: str) -> dict:
    """
    Scan an Office Open XML file (docx/xlsx/pptx) as a zip.
    Returns findings for macros, DDE, OLE.
    """
    findings = []

    try:
        zf = zipfile.ZipFile(path, 'r')
    except Exception as e:
        return {"findings": [],
                "error": f"could not open as zip: {e}"}

    names = zf.namelist()

    # 1. VBA macro
    macro_parts = [n for n in names
                   if 'vbaproject.bin' in n.lower()
                   or n.lower().endswith('.bas')
                   or n.lower().endswith('.cls')]
    if macro_parts:
        findings.append(_finding(
            "office_macro", "high",
            f"VBA macro parts found: {macro_parts[:3]}"))

    # 2. OLE embedded objects
    ole_parts = [n for n in names
                 if 'embeddings/' in n.lower()
                 or 'oleobject' in n.lower()
                 or n.lower().endswith('.bin')]
    # exclude vbaProject.bin already caught above
    ole_parts = [n for n in ole_parts
                 if 'vbaproject' not in n.lower()]
    if ole_parts:
        findings.append(_finding(
            "office_ole_object", "medium",
            f"OLE/embedded parts: {ole_parts[:3]}"))

    # 3. DDE fields — scan word/document.xml
    doc_xmls = [n for n in names
                if re.search(r'word/document|ppt/slides/slide\d',
                             n, re.IGNORECASE)]
    for xml_name in doc_xmls:
        try:
            content = zf.read(xml_name).decode('utf-8', errors='replace')
            # DDE field instruction pattern
            if re.search(r'DDEAUTO|<w:instrText[^>]*>\s*DDE',
                         content, re.IGNORECASE):
                findings.append(_finding(
                    "office_dde_field", "high",
                    f"DDE/DDEAUTO field found in {xml_name}"))
        except Exception:
            continue

    zf.close()
    return {"findings": findings}


# ══════════════════════════════════════════════════════════════════════════════
# F28 — Excel formula integrity
# ══════════════════════════════════════════════════════════════════════════════

def scan_xlsx_formulas(path: str) -> dict:
    """
    F28 — Excel formula detective.

    Loads the workbook TWICE:
      data_only=True  → resolved values
      data_only=False → raw formulas

    Financial cells (matching FINANCIAL_LABELS in their row header or
    column header) that contain a hardcoded value with NO formula are
    flagged manual_override_suspected.

    A banker seeing 'EBITDA = 450' typed manually (not =SUM(...))
    is a red flag the number may have been altered.
    """
    try:
        import openpyxl
    except ImportError:
        return {"findings": [], "manual_overrides": [],
                "error": "openpyxl not installed"}

    findings        = []
    manual_overrides = []

    try:
        wb_val  = openpyxl.load_workbook(path, data_only=True,  read_only=True)
        wb_form = openpyxl.load_workbook(path, data_only=False, read_only=True)
    except Exception as e:
        return {"findings": [], "manual_overrides": [],
                "error": f"could not open workbook: {e}"}

    for sheet_name in wb_val.sheetnames:
        ws_val  = wb_val[sheet_name]
        ws_form = wb_form[sheet_name]

        # build row-header map: row_index -> label in col A or B
        row_labels = {}
        for row in ws_val.iter_rows(min_col=1, max_col=2, values_only=True):
            for cell_val in row:
                if cell_val and isinstance(cell_val, str):
                    # store against the row number (approximate)
                    pass  # handled per-cell below

        rows_val  = list(ws_val.iter_rows())
        rows_form = list(ws_form.iter_rows())

        for r_idx, (row_v, row_f) in enumerate(
                zip(rows_val, rows_form)):
            # get row label from first text cell
            row_label = ""
            for cell in row_v:
                if cell.value and isinstance(cell.value, str):
                    row_label = cell.value
                    break

            for cell_v, cell_f in zip(row_v, row_f):
                value   = cell_v.value
                formula = cell_f.value

                # skip empty / text / already a formula
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(formula, str) and formula.startswith('='):
                    continue   # has a formula — fine

                # numeric value, no formula — check if it's a financial cell
                if isinstance(value, (int, float)):
                    label_to_check = row_label + " " + (
                        str(cell_v.column_letter) or "")
                    if FINANCIAL_LABELS.search(label_to_check):
                        coord = f"{sheet_name}!{cell_v.coordinate}"
                        manual_overrides.append({
                            "location": coord,
                            "value":    value,
                            "label":    row_label,
                        })
                        findings.append(_finding(
                            "manual_override_suspected", "high",
                            f"{coord} = {value!r} (label: '{row_label}') "
                            f"— hardcoded value, no formula backing",
                            block_id=coord))

    wb_val.close()
    wb_form.close()
    return {"findings": findings,
            "manual_overrides": manual_overrides}


# ── public combined scanner ───────────────────────────────────────────────────

def scan_office(path: str | Path) -> dict:
    """
    Run all Office security checks on one file.
    Routes xlsx to formula scanner too.
    """
    findings = []

    path_str = str(path)
    zip_result = scan_office_zip(path_str)
    findings  += zip_result.get("findings", [])

    manual_overrides = []
    if path_str.lower().endswith('.xlsx'):
        formula_result    = scan_xlsx_formulas(path)
        findings         += formula_result.get("findings", [])
        manual_overrides  = formula_result.get("manual_overrides", [])

    return {
        "findings":         findings,
        "manual_overrides": manual_overrides,
        "clean":            len(findings) == 0,
    }