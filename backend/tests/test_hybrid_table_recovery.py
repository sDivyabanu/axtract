"""Production geometry/routing tests; no benchmark answers or generated PDFs."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from models.document import BlockType, DocumentBlock
from services import raster_table_service as tables
from services.markdown_service import blocks_to_markdown
from services.table_service import enhance_table_block
from utils import deadline
from extractors.table_geometry import has_cell_rectangles, indexed_numeric_candidates


@pytest.fixture(autouse=True)
def budget(monkeypatch):
    monkeypatch.setattr(deadline, "remaining", lambda: 60.)


def fixture_grid(*, empty=False):
    image=Image.new("RGB",(400,330),"white")
    draw=ImageDraw.Draw(image)
    xs=[30,140,250,360];ys=[30,95,160,225,290]
    for x in xs:draw.line((x,ys[0],x,ys[-1]),fill="black",width=2)
    for y in ys:draw.line((xs[0],y,xs[-1],y),fill="black",width=2)
    tokens=[]
    for r in range(4):
        for c in range(3):
            if empty and (r,c)==(2,1):continue
            tokens.append(tables.Token((xs[c]+10,ys[r]+15,xs[c]+70,ys[r]+30),
                                       f"entry{r}{c}",f"source-{r}-{c}",.93))
    h,v=tables.line_masks(image)
    return image,tokens,h,v


def test_ruled_grid_empty_cells_and_determinism():
    image,tokens,h,v=fixture_grid(empty=True)
    boxes=tables.ruled_regions(h,v)
    assert len(boxes)==1
    a=tables.reconstruct(tokens,boxes[0],h,v)
    b=tables.reconstruct(list(reversed(tokens)),boxes[0],h,v)
    assert a["rows"]==b["rows"]
    assert len(a["rows"])==4 and len(a["rows"][0])==3
    assert a["rows"][2][1]=="" and not a["spans"]
    assert len(a["cell_bboxes"])==4


def test_wrapped_text_stays_in_its_ruled_cell():
    _,tokens,h,v=fixture_grid()
    tokens.append(tables.Token((40,68,100,80),"second line","wrap"))
    result=tables.reconstruct(tokens,tables.ruled_regions(h,v)[0],h,v)
    assert result["rows"][0][0]=="entry00\nsecond line"


def test_header_colspan_uses_missing_internal_rule_not_blank_text():
    _,tokens,h,v=fixture_grid()
    v[33:93,137:144]=0
    result=tables.reconstruct(tokens,(29,29,363,293),h,v)
    assert [0,0,1,2] in result["spans"]
    assert "entry00" in result["rows"][0][0] and "entry01" in result["rows"][0][0]
    assert result["rows"][0][1]==""
    assert result["cell_bboxes"][0][0][2] >= 249
    assert result["cell_bboxes"][0][1] is None


def test_rowspan_is_proven_by_missing_horizontal_segment():
    _,tokens,h,v=fixture_grid()
    h[92:99,33:138]=0
    result=tables.reconstruct(tokens,(29,29,363,293),h,v)
    assert [0,0,2,1] in result["spans"]
    assert "entry10" in result["rows"][0][0]
    assert result["cell_bboxes"][0][0][3] >= 159
    assert result["cell_bboxes"][1][0] is None


def test_irregular_l_shaped_merge_is_rejected():
    _,tokens,h,v=fixture_grid()
    v[33:93,137:144]=0
    h[92:99,143:248]=0
    assert tables.reconstruct(tokens,(29,29,363,293),h,v) is None


def aligned_tokens(text=lambda r,c:f"label{r}{c}"):
    return [tables.Token((40+c*110,45+r*50,100+c*110,60+r*50),text(r,c),f"s{r}{c}")
            for r in range(4) for c in range(3)]


def test_borderless_nonnumeric_grid_requires_independent_layout_evidence():
    zero=np.zeros((300,400),np.uint8);tokens=aligned_tokens()
    assert tables.reconstruct(tokens,(25,25,370,260),zero,zero) is None
    grid=tables.reconstruct(tokens,(25,25,370,260),zero,zero,layout_evidence=True)
    assert len(grid["rows"])==4 and len(grid["rows"][0])==3


def test_one_header_condition_is_not_an_aligned_equation():
    image,tokens,h,v=fixture_grid()
    v[:,137:144]=0
    v[:,247:254]=0
    tokens[0]=tables.Token(tokens[0].box,"limit=50%",tokens[0].source_id,.93)
    grid=tables.reconstruct(tokens,(29,29,363,293),h,v)
    assert grid is not None and grid["rows"][0][0]=="limit=50%"


def test_low_ocr_confidence_remains_reviewable_in_a_ruled_table():
    image,tokens,_,_=fixture_grid()
    tokens[0]=tables.Token(tokens[0].box,tokens[0].text,tokens[0].source_id,.4)
    blocks=source_blocks(tokens,image)
    tables.recover_page_tables(image,1,blocks,[],[])
    table=next(b for b in blocks if b.type==BlockType.TABLE)
    assert table.confidence==.4 and table.requires_review


@pytest.mark.parametrize("kind",["paragraph","newspaper","math"])
def test_plain_content_cannot_become_borderless_table(kind):
    zero=np.zeros((300,400),np.uint8)
    if kind=="math":tokens=aligned_tokens(lambda r,c:["x", "=",str(r)][c])
    elif kind=="paragraph":tokens=[tables.Token((30,30+r*30,350,45+r*30),"A full width paragraph with several words.",str(r)) for r in range(5)]
    else:tokens=[tables.Token((30+c*190,30+r*30,170+c*190,45+r*30),"Newspaper text in a column",str(r)+str(c)) for r in range(5) for c in range(2)]
    assert tables.reconstruct(tokens,(25,25,390,280),zero,zero,layout_evidence=True) is None


def source_blocks(tokens,image,route="scanned"):
    return [DocumentBlock(id=t.source_id,type=BlockType.PARAGRAPH,content=t.text,page=1,
               bbox=tuple(v/s for v,s in zip(t.box,(image.width,image.height,image.width,image.height))),
               extractor="rapidocr",confidence=t.confidence,metadata={"route":route}) for t in tokens]


def test_scanned_page_reuses_ocr_and_preserves_surrounding_prose(monkeypatch):
    image,tokens,_,_=fixture_grid()
    blocks=source_blocks(tokens,image)
    caption=DocumentBlock(id="caption",type=BlockType.PARAGRAPH,content="Caption below table",page=1,
                          bbox=(.1,.92,.8,.98),extractor="rapidocr",metadata={"route":"scanned"})
    blocks.append(caption)
    from extractors.ocr_extractor import OCRExtractor
    monkeypatch.setattr(OCRExtractor,"ocr_pil_image",lambda *a:pytest.fail("Must reuse existing OCR"))
    tables.recover_page_tables(image,1,blocks,[],[])
    found=[b for b in blocks if b.type==BlockType.TABLE]
    assert len(found)==1 and caption in blocks
    assert len(found[0].metadata["source_blocks"])==12
    md=blocks_to_markdown(blocks)
    assert md.count("entry00")==1 and "Caption below table" in md


def test_embedded_raster_table_on_digital_page_uses_crop_ocr(monkeypatch):
    image,tokens,_,_=fixture_grid()
    figure=DocumentBlock(id="image",type=BlockType.FIGURE,content="[image]",page=1,
                         bbox=(0,0,1,1),extractor="pymupdf",metadata={"route":"digital"})
    blocks=[figure]
    from extractors.ocr_extractor import OCRExtractor
    def ocr(self,crop,page):
        # Production candidate includes one pixel of connected-component padding.
        box=tables.ruled_regions(*tables.line_masks(image))[0]
        converted=[tables.Token(((t.box[0]-box[0]),(t.box[1]-box[1]),
                                 (t.box[2]-box[0]),(t.box[3]-box[1])),t.text,t.source_id,.93) for t in tokens]
        return source_blocks(converted,crop,"digital"),[]
    monkeypatch.setattr(OCRExtractor,"ocr_pil_image",ocr)
    tables.recover_page_tables(image,1,blocks,[],[figure.bbox])
    assert any(b.type==BlockType.TABLE for b in blocks)
    assert figure in blocks  # An encompassing picture may also contain a chart.


@pytest.mark.parametrize("raises",[True,False])
def test_ocr_failure_preserves_original_figure(monkeypatch,raises):
    image,_,_,_=fixture_grid()
    figure=DocumentBlock(id="figure",type=BlockType.FIGURE,content="[image]",page=1,
                        bbox=(0,0,1,1),extractor="pymupdf")
    from extractors.ocr_extractor import OCRExtractor
    def ocr(*a):
        if raises:raise RuntimeError("offline OCR failure")
        return [],["failed"]
    monkeypatch.setattr(OCRExtractor,"ocr_pil_image",ocr)
    blocks=[figure];tables.recover_page_tables(image,1,blocks,[],[figure.bbox])
    assert blocks==[figure]


def test_native_text_is_preferred_over_ocr(monkeypatch):
    image,tokens,_,_=fixture_grid()
    figure=DocumentBlock(id="f",type=BlockType.FIGURE,content="[image]",page=1,bbox=(0,0,1,1),extractor="pymupdf")
    from extractors.ocr_extractor import OCRExtractor
    monkeypatch.setattr(OCRExtractor,"ocr_pil_image",lambda *a:pytest.fail("Native text must win"))
    words=[(*t.box,t.text) for t in tokens];blocks=[figure]
    tables.recover_page_tables(image,1,blocks,[],[figure.bbox],words)
    assert blocks[-1].extractor=="native_raster_grid"


def test_partial_paragraph_overlap_is_preserved():
    image,tokens,_,_=fixture_grid();blocks=source_blocks(tokens,image)
    crossing=DocumentBlock(id="cross",type=BlockType.PARAGRAPH,content="Body crossing the table edge",page=1,
                          bbox=(.01,.02,.3,.15),extractor="pymupdf")
    blocks.append(crossing);original=list(blocks)
    tables.recover_page_tables(image,1,blocks,[],[])
    assert blocks==original


def test_existing_math_or_chart_ownership_is_not_replaced():
    image,tokens,_,_=fixture_grid()
    for kind in [BlockType.EQUATION,BlockType.CHART,BlockType.TABLE]:
        blocks=source_blocks(tokens,image);protected=DocumentBlock(id="protected",type=kind,content="keep",
                    page=1,bbox=(.1,.1,.9,.8),extractor="existing");blocks.append(protected);original=list(blocks)
        tables.recover_page_tables(image,1,blocks,[],[])
        assert blocks==original


def test_no_allocation_above_render_caps():
    for size in [(2501,1),(2250,2250),(1,99999)]:
        with pytest.raises(ValueError):tables.line_masks(SimpleNamespace(size=size,width=size[0],height=size[1]))


def test_low_budget_does_not_allocate_or_ocr(monkeypatch):
    monkeypatch.setattr(deadline,"remaining",lambda:7.)
    monkeypatch.setattr(tables,"line_masks",lambda *a:pytest.fail("Late work must be skipped"))
    tables.recover_page_tables(None,1,[],[],[])


def test_feature_flag_preserves_fallback(monkeypatch):
    monkeypatch.setenv("AXTRACT_RASTER_TABLES","0")
    monkeypatch.setattr(tables,"line_masks",lambda *a:pytest.fail("Disabled"))
    tables.recover_page_tables(None,1,[],[],[])


def numeric_native_fixture():
    words=[]
    for c,label in enumerate(["Item","Measurement","Score"]):
        words.append((40+c*100,17,95+c*100,27,label,0,c,0))
    for r in range(6):
        for c,text in enumerate([str(r+1),str(20+r),str(.5+r/100)]):
            words.append((40+c*100,40+r*16,70+c*100,50+r*16,text,r+1,c,0))
    return words,[{"rect":(30,30,310,31)}]


def test_native_indexed_numeric_grid_preserves_header_and_cells():
    words,drawings=numeric_native_fixture()
    found=indexed_numeric_candidates(words,drawings)
    assert len(found)==1 and len(found[0]["rows"])==7
    assert found[0]["rows"][0]==["Item","Measurement","Score"]
    assert found[0]["rows"][1]==["1","20","0.5"]
    assert len(found[0]["cell_bboxes"])==7


@pytest.mark.parametrize("defect",["no_rule","not_consecutive","unowned_text"])
def test_native_number_lists_without_complete_evidence_are_rejected(defect):
    words,drawings=numeric_native_fixture()
    if defect=="no_rule":drawings=[]
    elif defect=="not_consecutive":
        words=[(*w[:4],str(int(w[4])*3),*w[5:]) if w[5]>0 and w[6]==0 else w for w in words]
    else:words.append((110,70,125,80,"unowned",99,0,0))
    assert not indexed_numeric_candidates(words,drawings)


def test_native_repeated_closed_cell_discovery_needs_adjacent_columns():
    drawings=[{"items":[("re",(c*61,r*30,c*61+60,r*30+30),1)]} for r in range(3) for c in range(2)]
    assert has_cell_rectangles(drawings)
    assert not has_cell_rectangles(drawings[::2])


@pytest.mark.parametrize("pdf",[
    "2d932ac96ad0d8ccf81a56bd105fa18fdcbb_pg7.pdf",
    "3c0b540d8dab2598af4386b4034c2021f46b_pg4_pg1.pdf",
])
def test_existing_native_pdf_runs_the_production_extractor(pdf):
    path=Path(__file__).resolve().parents[2]/"benchmarks/olmOCR-bench/bench_data/pdfs/tables"/pdf
    if not path.exists():pytest.skip("Existing official PDF unavailable")
    from extractors.pymupdf_extractor import PyMuPDFExtractor
    result=PyMuPDFExtractor().extract(path)
    assert any(b.type==BlockType.TABLE and b.metadata.get("row_count",0)>=3 for b in result.blocks)


def test_official_parser_accepts_html_spans_and_escaped_content():
    root=Path(__file__).resolve().parents[2]
    path=root/"axtract-olmocr-data/scorer/olmocr/bench/table_parsing.py"
    if not path.exists():pytest.skip("Local official scorer checkout unavailable")
    b=DocumentBlock(id="t",type=BlockType.TABLE,content="",page=1,extractor="test",
          metadata={"rows":[["Grouped <safe>","","Other"],["A & B","v|x","Y"],["","q","Z"]],
                    "merged_cells":{"source":"extractor","merged_regions":[[0,0,1,2],[1,0,2,1]]}})
    md=blocks_to_markdown([enhance_table_block(b)])
    assert '&lt;safe&gt;' in md and 'rowspan="2"' in md and 'colspan="2"' in md
    # The official scorer has its own Python 3.12 runtime. Do not install its
    # dependencies into the application's Python 3.14 environment.
    python=root/"axtract-olmocr-data/python/cpython-3.12.15-windows-x86_64-none/python.exe"
    packages=root/"axtract-olmocr-data/scorer312/Lib/site-packages"
    if not python.exists():pytest.skip("Local official scorer runtime unavailable")
    program=f"""
import sys, importlib.util
sys.path.insert(0,{str(packages)!r})
spec=importlib.util.spec_from_file_location('official_tables',{str(path)!r})
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
parsed=module.parse_html_tables(sys.stdin.read())
assert len(parsed)==1
assert parsed[0].cell_text[(0,0)]=='Grouped <safe>'
assert parsed[0].cell_text[(1,0)]=='A & B'
assert (0,0) in parsed[0].top_heading_relations(1,1)
"""
    result=subprocess.run([str(python),"-B","-X","utf8","-c",program],input=md,
                          text=True,encoding="utf8",capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
