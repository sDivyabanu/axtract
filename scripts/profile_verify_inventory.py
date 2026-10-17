"""Time each operation of the Verify PDF inventory on one PDF (local diagnostic, read-only).
usage: profile_verify_inventory.py PDF
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
pdf = Path(sys.argv[1])


def timed(label, fn):
    t = time.perf_counter()
    out = fn()
    print(f"{label:48s} {time.perf_counter() - t:7.2f}s")
    return out


import pdfplumber  # noqa: E402
import pypdfium2 as pdfium  # noqa: E402
import pypdfium2.raw as raw  # noqa: E402

doc = timed("pdfium open", lambda: pdfium.PdfDocument(str(pdf)))
page = doc[0]
timed("pdfium text", lambda: page.get_textpage().get_text_range())
timed("pdfium image objects", lambda: list(page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE])))
pl = timed("pdfplumber open", lambda: pdfplumber.open(str(pdf)))
pp = timed("pdfplumber page object (lazy)", lambda: pl.pages[0])
timed("pdfplumber layout parse (page.objects)", lambda: pp.objects)
print("   curves:", len(pp.curves), "lines:", len(pp.lines), "rects:", len(pp.rects), "chars:", len(pp.chars))
timed("pdfplumber extract_words", lambda: pp.extract_words(use_text_flow=False))
timed("pdfplumber find_tables (default strategy)", lambda: pp.find_tables())
