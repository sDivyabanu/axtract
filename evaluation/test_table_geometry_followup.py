"""Pure geometry regressions; no benchmark PDFs or expected-answer files generated."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from extractors.table_geometry import split_stacked_rows,native_line_numeric_candidates


def stacked():
    words=[]
    for row,(label,a,b) in enumerate((("Alpha","2","6"),("Beta","3","7"),("Gamma","4","8"))):
        for col,value in enumerate((label,a,b)):
            words.append((col*100+2,row*15+2,col*100+40,row*15+12,value,0,row*3+col,0))
    rows=[["Alpha\nBeta\nGamma","2\n3\n4","6\n7\n8"]]
    boxes=[[(c*100,0,c*100+90,45) for c in range(3)]]
    return rows,boxes,words


def test_aligned_stacked_rows_split_without_changing_values():
    rows,boxes,words=stacked()
    result,geometry,repairs=split_stacked_rows(rows,boxes,words)
    assert len(result)==len(geometry)==3 and repairs
    assert result[1]==["Beta","3","7"]
    assert sorted(" ".join(str(v) for row in result for v in row).split())==sorted(" ".join(rows[0]).split())


def test_wrapped_label_with_single_numeric_values_is_not_split():
    rows,boxes,words=stacked()
    rows[0][1]="2";rows[0][2]="6"
    words=[w for w in words if w[0]<100 or w[1]==2]
    assert split_stacked_rows(rows,boxes,words)[0]==rows


def test_explicit_merged_cell_is_not_split():
    rows,boxes,words=stacked();boxes[0][0]=None
    assert split_stacked_rows(rows,boxes,words)[0]==rows


def test_cell_source_disagreement_preserves_original_grid():
    rows,boxes,words=stacked();rows[0][0]+=" absent"
    assert split_stacked_rows(rows,boxes,words)[0]==rows


def test_native_line_grid_does_not_absorb_adjacent_prose():
    _,_,words=stacked()
    prose=[(-150,r*15+1,-30,r*15+14,"A neighbouring sentence.",9,r,0) for r in range(3)]
    result=native_line_numeric_candidates(words+prose)
    assert len(result)==1
    assert result[0]["bbox"][0]==2
    assert all("neighbouring" not in value for row in result[0]["rows"] for value in row)


def test_unrepresented_source_word_rejects_candidate():
    _,_,words=stacked()
    words.append((55,16,80,29,"extra",5,9,0))
    assert native_line_numeric_candidates(words)==[]



def test_local_fragment_cannot_shadow_wider_source_grid():
    from extractors.table_geometry import shadows_complete_grid
    fragment={"bbox":(0,0,200,50),"rows":[["A","1","2"]]*3}
    complete={"bbox":(0,0,400,150),"rows":[["A","1","2","3","4"]]*8}
    assert shadows_complete_grid(fragment,[complete])


def test_local_fragment_cannot_split_same_column_table_into_sections():
    from extractors.table_geometry import shadows_complete_grid
    fragment={"bbox":(0,0,200,50),"rows":[["A","1","2"]]*3}
    complete={"bbox":(0,0,200,150),"rows":[["A","1","2"]]*8}
    assert shadows_complete_grid(fragment,[complete])



def test_image_blocks_do_not_shift_source_text_provenance():
    from extractors.pymupdf_extractor import _align_word_block_numbers
    raw=[(0,10,20,20,"first",0,0,0),(0,30,20,40,"second",1,0,0)]
    page={"blocks":[{"type":1},{"type":0},{"type":1},{"type":0}]}
    fixed=_align_word_block_numbers(raw,page)
    assert [w[5] for w in fixed]==[1,3]
    assert [w[:5] for w in fixed]==[w[:5] for w in raw]


def test_line_height_uses_word_bottom_not_word_top():
    from extractors.pymupdf_extractor import _word_lines
    result=_word_lines([(0,10,20,22,"source",0,0,0),(22,10,40,22,"text",0,0,1)])
    assert result[0]==[(10,22,"source text")]



def test_detached_footer_ignores_nonoverlapping_side_annotation():
    from models.document import DocumentBlock,BlockType
    from services.layout_service import assign_reading_order
    def paragraph(id,text,box):
        return DocumentBlock(id=id,type=BlockType.PARAGRAPH,content=text,page=1,bbox=box,
          extractor="pymupdf",metadata={"route":"digital","font_size":8,"body_font_size":12})
    body=paragraph("body","Body text",(.2,.2,.8,.7))
    side=paragraph("side","Vertical side note",(.02,.5,.04,.89))
    footer=paragraph("footer","Publication details and contact information",(.1,.9,.9,.93))
    result=assign_reading_order([body,side,footer])
    assert footer.type==BlockType.FOOTER
    assert footer.content=="Publication details and contact information"


def test_math_or_explicit_footnote_is_not_new_detached_footer():
    from models.document import DocumentBlock,BlockType
    from services.layout_service import assign_reading_order
    for guard in ({"text_layer_math":True},{"footnote_refs":["1"]}):
        body=DocumentBlock(id="body",type=BlockType.PARAGRAPH,content="Body",page=1,extractor="pymupdf",bbox=(.1,.2,.9,.7))
        note=DocumentBlock(id="note",type=BlockType.PARAGRAPH,content="Preserved note",page=1,extractor="pymupdf",bbox=(.1,.9,.9,.93),
             metadata={"route":"digital","font_size":8,"body_font_size":12,**guard})
        assign_reading_order([body,note])
        assert note.type==BlockType.PARAGRAPH
