"""Table rendering regressions without loading app, OCR or vision models."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from models.document import BlockType, DocumentBlock
from services.markdown_service import blocks_to_markdown


def table(rows, content="table"):
    return DocumentBlock(id="t",type=BlockType.TABLE,content=content,page=1,extractor="fixture",metadata={"rows":rows})


def test_multiline_cells_are_one_physical_row():
    md = blocks_to_markdown([table([["CATEGORY", "POINTS\r\nEARNED"],["Sites",9]])])
    assert "| CATEGORY | POINTS EARNED |" in md
    assert len(md.splitlines()) == 3


def test_missing_cells_are_padded_without_shifting():
    md = blocks_to_markdown([table([["a","b"],[None,0,"tail"],["last"]])])
    assert "| a | b |  |" in md
    assert "|  | 0 | tail |" in md
    assert "| last |  |  |" in md
    assert all(line.count("|") == 4 for line in md.splitlines())


def test_pipes_and_html_are_literal_cell_text():
    md = blocks_to_markdown([table([["name","value"],["a | b", "<tag> & value"]])])
    assert "<table>" in md
    assert "<th>a | b</th>" in md
    assert "&lt;tag&gt; &amp; value" in md


def test_rows_render_even_when_content_is_empty():
    assert "| a | b |" in blocks_to_markdown([table([["a","b"]],content="")])


def test_non_table_serialization_unchanged():
    for kind, content, expected in [(BlockType.PARAGRAPH,"body","body\n"),(BlockType.HEADING,"Title","# Title\n"),(BlockType.EQUATION,"x+y","$$\nx+y\n$$\n")]:
        b=DocumentBlock(id="b",type=kind,content=content,page=1,extractor="fixture")
        assert blocks_to_markdown([b]) == expected


from services.table_service import enhance_table_block


def test_inferred_missing_cells_do_not_add_fake_span_text():
    block=enhance_table_block(table([["A",None,"C"],["1",None,"3"]]))
    md=blocks_to_markdown([block])
    assert "<colspan:" not in md and "<rowspan:" not in md
    assert "| 1 |  | 3 |" in md
    assert block.metadata["merged_cells"]["source"]=="inferred_missing_cells"


def test_explicit_spans_use_semantic_html():
    block=table([["Group",None,"Other"],["A","B","C"]])
    block.metadata["merged_cells"]={"has_merged_cells":True,"merged_regions":[(0,0,1,2)]}
    md=blocks_to_markdown([enhance_table_block(block)])
    assert '<th colspan="2">Group</th>' in md
    assert "<colspan:" not in md


def test_conflicting_span_cannot_delete_source_value():
    block=table([["Group","Keep"],["A","B"]])
    block.metadata["merged_cells"]={"has_merged_cells":True,"source":"extractor","merged_regions":[(0,0,1,2)]}
    md=blocks_to_markdown([block])
    assert "Keep" in md
    assert "colspan" not in md


def test_explicit_rowspan_preserves_lower_row_coordinates():
    block=table([["Label","Value"],[None,"Next"]])
    block.metadata["merged_cells"]={"has_merged_cells":True,"source":"extractor","merged_regions":[(0,0,2,1)]}
    md=blocks_to_markdown([block])
    assert '<th rowspan="2">Label</th>' in md
    assert '<tr><td>Next</td></tr>' in md
