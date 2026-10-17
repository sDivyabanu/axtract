"""A page-sized background image must never swallow the text on its page (multi_column/0b2e551b…_page_9_pg1.pdf:
23 paragraphs vanished and 5 official order assertions failed)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models.document import BlockType, DocumentBlock
from services.layout_service import assign_reading_order
from services.markdown_service import blocks_to_markdown

WORDS = " ".join(f"word{i}" for i in range(45))


def blk(bid, btype, bbox, text=WORDS, route="digital"):
    return DocumentBlock(id=bid, type=btype, content=text, page=1, bbox=bbox, confidence=None, extractor="pymupdf",
                         metadata={"route": route})


def two_columns():
    left = [blk(f"L{i}", BlockType.PARAGRAPH, (0.10, 0.10 + 0.25 * i, 0.45, 0.30 + 0.25 * i), f"L{i} {WORDS}") for i in range(3)]
    right = [blk(f"R{i}", BlockType.PARAGRAPH, (0.55, 0.10 + 0.25 * i, 0.90, 0.30 + 0.25 * i), f"R{i} {WORDS}") for i in range(3)]
    return left + right


def ids(blocks):
    return [b.id for b in blocks]


def test_full_page_background_figure_keeps_every_paragraph_in_column_order():
    bg = blk("bg", BlockType.FIGURE, (0.0, 0.0, 1.0, 1.0), "[image]")
    out = assign_reading_order(two_columns() + [bg])
    assert sorted(ids(out)) == sorted(["bg", "L0", "L1", "L2", "R0", "R1", "R2"])
    assert ids(out) == ["bg", "L0", "L1", "L2", "R0", "R1", "R2"]


def test_no_block_is_duplicated_or_reordered_without_a_background():
    out = assign_reading_order(two_columns())
    assert ids(out) == ["L0", "L1", "L2", "R0", "R1", "R2"]


def test_text_inside_a_wide_spanning_block_is_kept_exactly_once():
    wide_table = blk("tbl", BlockType.TABLE, (0.05, 0.40, 0.95, 0.60), "| a | b |")
    inside = blk("cap", BlockType.PARAGRAPH, (0.10, 0.45, 0.45, 0.55), f"cap {WORDS}")
    blocks = two_columns() + [wide_table, inside]
    out = assign_reading_order(blocks)
    assert sorted(ids(out)) == sorted(ids(blocks)) and len(set(ids(out))) == len(out)


def test_a_normal_picture_in_a_column_is_not_treated_as_a_page_background():
    fig = blk("fig", BlockType.FIGURE, (0.10, 0.10, 0.45, 0.30), "[image]")
    out = assign_reading_order(two_columns() + [fig])
    assert len(out) == 7 and ids(out).index("R0") > ids(out).index("L2")


def test_reading_order_indexes_stay_contiguous():
    bg = blk("bg", BlockType.FIGURE, (0.0, 0.0, 1.0, 1.0), "[image]")
    out = assign_reading_order(two_columns() + [bg])
    assert [b.reading_order for b in out] == list(range(len(out)))


PDF = Path(r"C:\Users\sdivy\wdev\Projects\axtract\benchmarks\olmOCR-bench\bench_data\pdfs\multi_column"
           r"\0b2e551bbc33b030f810311118e2088d7ae3_page_9_pg1.pdf")


@pytest.mark.skipif(not PDF.exists(), reason="official benchmark PDF not present")
def test_reported_patent_page_keeps_its_text():
    from services.pdf_router import PDFRouter
    from utils import deadline

    deadline._deadline.set(None)
    result = PDFRouter().extract(PDF)
    ordered = assign_reading_order(result.blocks)
    assert len(ordered) == len(result.blocks) >= 20
    md = blocks_to_markdown(ordered)
    assert "Inner core layer 440 may have a surface" in md
