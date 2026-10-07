# tests/gauntlet/run_gauntlet.py
"""
Runs all security scanners against the 8 gauntlet files.
Prints a PASS/FAIL table.

Run from backend/:
    python tests/gauntlet/run_gauntlet.py
"""
import sys, os, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from unicode_guard   import clean_and_flag
from hidden_content  import scan_xlsx, scan_pdf_page
from pdf_guard       import scan_pdf
from office_scan     import scan_office
from active_links    import scan_office_links

FILES = Path(__file__).parent / "files"

# ── expected findings per file ────────────────────────────────────────────────

EXPECTED = {
    "hidden_white_text.pdf": [
        "hidden_text_white",
        "prompt_injection_suspected",
    ],
    "pdf_js.pdf": [
        "pdf_javascript",
        "pdf_auto_action",
    ],
    "hidden_sheet.xlsx": [
        "hidden_sheet",
        "prompt_injection_suspected",
    ],
    "unicode_tricks.docx": [
        "zero_width_chars",
        "bidi_override",
        "homoglyph_suspected",
        "prompt_injection_suspected",
    ],
    "macro.docm": [
        "office_macro",
    ],
    "dde.docx": [
        "office_dde_field",
    ],
    "remote_template.docx": [
        "remote_template",
    ],
    "formula_override.xlsx": [
        "manual_override_suspected",
    ],
}


# ── scanners ──────────────────────────────────────────────────────────────────

def run_hidden_white_text_pdf(path):
    import pymupdf
    doc = pymupdf.open(str(path))
    findings = []
    for i, page in enumerate(doc):
        r = scan_pdf_page(page, i)
        findings += r["findings"]
    doc.close()
    return findings


def run_pdf_js(path):
    return scan_pdf(str(path))["findings"]


def run_hidden_sheet_xlsx(path):
    return scan_xlsx(str(path))["findings"]


def run_unicode_tricks_docx(path):
    import docx as dx
    d = dx.Document(str(path))
    findings = []
    for para in d.paragraphs:
        r = clean_and_flag(para.text, "para")
        findings += r["findings"]
    return findings


def run_macro_docm(path):
    return scan_office(str(path))["findings"]


def run_dde_docx(path):
    return scan_office(str(path))["findings"]


def run_remote_template_docx(path):
    return scan_office_links(str(path))["findings"]


def run_formula_override_xlsx(path):
    return scan_office(str(path))["findings"]


RUNNERS = {
    "hidden_white_text.pdf":  run_hidden_white_text_pdf,
    "pdf_js.pdf":             run_pdf_js,
    "hidden_sheet.xlsx":      run_hidden_sheet_xlsx,
    "unicode_tricks.docx":    run_unicode_tricks_docx,
    "macro.docm":             run_macro_docm,
    "dde.docx":               run_dde_docx,
    "remote_template.docx":   run_remote_template_docx,
    "formula_override.xlsx":  run_formula_override_xlsx,
}


# ── runner ────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "="*72)
    print("  AXTRACT SECURITY GAUNTLET")
    print("="*72)
    print(f"  {'FILE':<30} {'EXPECTED':<28} {'STATUS':<6} {'TIME'}")
    print("-"*72)

    total = passed = failed = 0

    for filename, expected_types in EXPECTED.items():
        path = FILES / filename
        if not path.exists():
            print(f"  {filename:<30} {'—':<28} SKIP   file not found")
            continue

        runner = RUNNERS.get(filename)
        if not runner:
            continue

        start = time.time()
        try:
            findings = runner(path)
            elapsed = time.time() - start
            found_types = {f["type"] for f in findings}

            # check all expected types present
            missing = [t for t in expected_types if t not in found_types]

            for exp in expected_types:
                total += 1
                status = "PASS" if exp in found_types else "FAIL"
                if status == "PASS":
                    passed += 1
                else:
                    failed += 1
                print(f"  {filename:<30} {exp:<28} {status:<6} "
                      f"{elapsed*1000:.0f}ms")

        except Exception as e:
            elapsed = time.time() - start
            print(f"  {filename:<30} {'ERROR':<28} FAIL   {e}")
            failed += len(expected_types)
            total  += len(expected_types)

    print("="*72)
    print(f"  RESULT: {passed}/{total} checks passed  "
          f"({'100%' if total==passed else f'{passed/total*100:.0f}%'})")
    print("="*72 + "\n")


if __name__ == "__main__":
    main()