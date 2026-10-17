"""Geometry controls for native table discovery and chart rejection."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from extractors.table_geometry import has_ruled_grid
from extractors.pymupdf_extractor import _has_local_chart_signal


def test_zero_area_rules_are_not_chart_marks():
    rules=[{"rect":(0,y,100,y)} for y in (0,50,100)]
    rules += [{"rect":(x,0,x,100)} for x in (0,50,100)]
    assert has_ruled_grid(rules,(0,0,100,100))


def test_segmented_cell_rules_and_shading_are_a_table():
    rules=[]
    for y in range(0,100,10):
        for x in (0,50):
            rules.append({"rect":(x,y,x+50,y+3)})
        for x in (0,50,100):
            rules.append({"rect":(x,y,x+1,y+10)})
    rules += [{"rect":(0,y,50,y+8)} for y in range(0,100,10)]
    assert has_ruled_grid(rules,(0,0,100,100))
    words=[(5,i*10,15,i*10+5,str(i),i,0,0) for i in range(8)]
    assert not _has_local_chart_signal(words,rules,(0,0,100,100))


def test_bar_chart_with_axes_and_gridlines_stays_chart():
    drawings=[{"rect":(0,y,100,y)} for y in (0,20,40,60,80,100)]
    drawings += [{"rect":(0,0,0,100)}]
    drawings += [{"rect":(x,100-h,x+8,100)} for x,h in [(5,30),(16,45),(27,55),(38,25),(49,60),(60,40),(71,50),(82,35)]]
    words=[(x,100-h-8,x+7,100-h-1,str(h),i,0,0) for i,(x,h) in enumerate([(5,30),(16,45),(27,55),(38,25),(49,60),(60,40),(71,50),(82,35)])]
    assert not has_ruled_grid(drawings,(0,0,100,100))
    assert _has_local_chart_signal(words,drawings,(0,0,100,100))


from extractors.table_geometry import numeric_table_candidates, physical_rows


def word(text,x,y,width=18,block=0):
    return (x,y,x+width,y+10,text,block,0,0)


def numeric_grid(cols=3,rows=5):
    words=[]
    for r in range(rows):
        words.append(word("Label"+chr(65+r),10,30+r*15,width=42,block=r*10))
        for c in range(cols):
            text=str((r+1)*(c+1))
            # All native line IDs are zero, one distinct block per cell.
            words.append(word(text,100+c*55-len(text)*4,30+r*15,width=len(text)*4,block=r*10+c+1))
    return words


def test_wide_borderless_grid_does_not_require_pdf_line_ids_or_borders():
    candidates=numeric_table_candidates(numeric_grid(cols=8))
    assert len(candidates)==1
    assert len(candidates[0]["rows"][0])==9
    assert candidates[0]["rows"][1][1:]==[str(2*(c+1)) for c in range(8)]


def test_sparse_cell_positions_are_kept():
    words=numeric_grid()
    words=[w for w in words if not (w[4]=="6" and w[0]>170 and w[1]==45)]
    candidate=numeric_table_candidates(words)[0]
    assert candidate["rows"][1]==["LabelB","2","4",""]


def test_neighbouring_prose_column_is_not_consumed():
    words=numeric_grid()
    shifted=[(w[0]+300,w[1],w[2]+300,w[3],*w[4:]) for w in words]
    shifted += [word("Body prose remains",10,30+r*15,width=200) for r in range(5)]
    candidate=numeric_table_candidates(shifted)[0]
    assert candidate["bbox"][0]>=300
    assert not any("Body" in c for row in candidate["rows"] for c in row)


def test_prose_and_numbered_lists_are_not_numeric_grids():
    prose=[word("Sentence",10,r*15,width=50) for r in range(5)]
    prose += [word(str(r),75,r*15) for r in range(5)]
    assert numeric_table_candidates(prose)==[]


def test_numeric_values_and_symbols_are_preserved_verbatim():
    words=numeric_grid()
    words=[(*w[:4], "1,234.50%" if w[4]=="1" and w[0]>90 else w[4], *w[5:]) for w in words]
    candidate=numeric_table_candidates(words)[0]
    assert "1,234.50%" in candidate["rows"][0]


from extractors.table_geometry import ruled_text_candidates


def test_segmented_horizontal_borders_preserve_multiline_text_cells():
    # Three horizontal borders have the same three-column cell breakpoints.
    drawings=[{"rect":(x,y,x+100,y+1)} for y in (10,30,160) for x in (0,100,200)]
    words=[]
    for r in range(5):
        for c in range(3):
            words.append(word("cell"+chr(65+r)+chr(65+c),c*100+5,15+r*28,width=60,block=r*10+c))
        if r==2:
            words.append(word("continued",205,15+r*28+11,width=60,block=r*10+2))
    candidates=ruled_text_candidates(words,drawings)
    assert len(candidates)==1
    assert candidates[0]["rows"][2][2]=="cellCC continued"
    assert len(candidates[0]["rows"])==5


def test_single_rule_prose_is_not_a_table():
    drawings=[{"rect":(0,y,300,y+1)} for y in (10,30,160)]
    assert ruled_text_candidates(numeric_grid(),drawings)==[]


from extractors.table_geometry import ruled_grid_regions


def test_non_numeric_ruled_table_reaches_native_detector():
    import pymupdf
    from extractors.pymupdf_extractor import PyMuPDFExtractor
    from models.document import BlockType
    doc=pymupdf.open();page=doc.new_page(width=400,height=400)
    for y in [40,80,120,160]:
        page.draw_line((30,y),(330,y))
    for x in [30,130,230,330]:
        page.draw_line((x,40),(x,160))
    for r in range(3):
        for c in range(3):
            page.insert_text((35+100*c,65+40*r),chr(65+r)+chr(65+c),fontsize=10)
    assert ruled_grid_regions(page.get_drawings())
    blocks=PyMuPDFExtractor()._extract_page(page,1)
    tables=[b for b in blocks if b.type==BlockType.TABLE]
    assert len(tables)==1 and tables[0].metadata["rows"]==[["AA","AB","AC"],["BA","BB","BC"],["CA","CB","CC"]]
    doc.close()


def test_axes_and_horizontal_guides_are_not_a_ruled_grid():
    drawings=[{"rect":(0,y,100,y)} for y in [0,20,40,60,80,100]]
    drawings.append({"rect":(0,0,0,100)})
    assert ruled_grid_regions(drawings)==[]



def test_line_chart_on_full_grid_is_not_treated_as_table():
    from pymupdf import Point
    drawings=[{"rect":(0,y,100,y)} for y in (0,25,50,75,100)]
    drawings += [{"rect":(x,0,x,100)} for x in (0,25,50,75,100)]
    drawings.append({"rect":(10,15,90,85),"items":[
        ("l",Point(10,85),Point(30,15)),("l",Point(30,15),Point(60,65)),("l",Point(60,65),Point(90,25))]})
    words=[word(str(i),i*10+3,90,width=5) for i in range(6)]
    assert has_ruled_grid(drawings,(0,0,100,100))
    assert _has_local_chart_signal(words,drawings,(0,0,100,100))


def test_single_slanted_header_border_is_not_a_plot():
    from pymupdf import Point
    drawings=[{"rect":(0,y,100,y)} for y in (0,50,100)]
    drawings += [{"rect":(x,0,x,100)} for x in (0,50,100)]
    drawings.append({"rect":(0,0,50,50),"items":[("l",Point(0,0),Point(50,50))]})
    assert not _has_local_chart_signal([word(str(i),i*10+3,90,width=5) for i in range(6)],drawings,(0,0,100,100))



def test_reconstructed_grid_conserves_all_contained_source_words():
    from collections import Counter
    words=numeric_grid(cols=5)
    for candidate in numeric_table_candidates(words):
        a,b,c,d=candidate["bbox"]
        source=Counter(str(w[4]) for w in words if w[0]>=a-1 and w[2]<=c+1 and w[1]>=b-1 and w[3]<=d+1)
        output=Counter(" ".join(cell for row in candidate["rows"] for cell in row).split())
        assert not (source-output)



def test_native_gridded_line_chart_remains_a_reviewable_figure():
    import pymupdf
    from extractors.pymupdf_extractor import PyMuPDFExtractor
    from models.document import BlockType
    doc=pymupdf.open();page=doc.new_page(width=400,height=400)
    for y in [30,90,150,210,270]:
        page.draw_line((30,y),(330,y))
    for x in [30,90,150,210,270,330]:
        page.draw_line((x,30),(x,270))
    shape=page.new_shape()
    shape.draw_polyline([(60,225),(140,75),(220,200),(300,90)])
    shape.finish(color=(0,0,0));shape.commit()
    for x,y,text in [(40,50,"10"),(75,50,"20"),(120,50,"30"),(160,90,"40"),(200,130,"50"),(240,170,"60")]:
        page.insert_text((x,y),text,fontsize=9)
    blocks=PyMuPDFExtractor()._extract_page(page,1)
    assert not any(b.type==BlockType.TABLE for b in blocks)
    assert any(b.type==BlockType.FIGURE and "chart_signal" in b.metadata.get("reason_flags",[]) for b in blocks)
    doc.close()
