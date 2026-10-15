from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from benchmark_axtract_olmocr import has_complete_output


def test_resume_only_skips_pdfs_with_all_page_outputs(tmp_path):
    import pymupdf

    pdf_path = tmp_path / "input" / "two-pages.pdf"
    pdf_path.parent.mkdir()
    document = pymupdf.open()
    document.new_page()
    document.new_page()
    document.save(pdf_path)
    document.close()

    relative = Path("category") / pdf_path.name
    output = tmp_path / "outputs"
    target = output / "category"
    target.mkdir(parents=True)

    assert not has_complete_output(pdf_path, relative, output)
    (target / "two-pages_pg1_repeat1.md").write_text("first page", encoding="utf-8")
    assert not has_complete_output(pdf_path, relative, output)
    (target / "two-pages_pg2_repeat1.md").write_text("second page", encoding="utf-8")
    assert has_complete_output(pdf_path, relative, output)
