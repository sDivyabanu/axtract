"""Scanned math ownership, bounded fallback and semantic-normalization tests."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import pytest
from PIL import Image
from extractors.base import ExtractionResult
from models.document import DocumentBlock,BlockType
from services import scanned_math
from services.vision import pix2text_mfr
from services.vision.line_math import tidy_latex
from services.vision.latex import validate_latex
from services.markdown_service import blocks_to_markdown


def source():
    block=DocumentBlock(id="ocr",type=BlockType.PARAGRAPH,content="y=2",page=1,bbox=(.1,.1,.9,.2),
                        extractor="rapidocr",metadata={"route":"scanned"})
    return ExtractionResult(page_count=1,blocks=[block])


def setup(monkeypatch):
    monkeypatch.setenv("AXTRACT_SCANNED_MATH","1")
    monkeypatch.setattr(scanned_math,"available_memory_bytes",lambda:2*1024**3)
    monkeypatch.setattr(pix2text_mfr,"mfr_status",lambda:{"available":True})
    monkeypatch.setattr(scanned_math.deadline,"remaining",lambda:60)
    monkeypatch.setattr(scanned_math,"hidden_scan_words",lambda *a:{})
    monkeypatch.setattr(scanned_math,"segment_lines",lambda image:[(20,20,180,40)]*2)
    monkeypatch.setattr("services.preview_service.render_page_pil",lambda *a:Image.new("RGB",(200,200),"white"))


def test_disabled_mode_never_loads_model(monkeypatch):
    monkeypatch.setenv("AXTRACT_SCANNED_MATH","0")
    monkeypatch.setattr(pix2text_mfr,"mfr_status",lambda:pytest.fail("disabled mode queried model"))
    r=source();scanned_math.route_scanned_math(r,Path("unused"));assert len(r.blocks)==1


def test_missing_weights_preserve_original(monkeypatch):
    setup(monkeypatch);monkeypatch.setattr(pix2text_mfr,"mfr_status",lambda:{"available":False})
    r=source();scanned_math.route_scanned_math(r,Path("unused"));assert len(r.blocks)==1
    assert r.blocks[0].content=="y=2" and "y=2" in blocks_to_markdown(r.blocks)


def test_equation_has_source_and_no_duplicate_or_lost_ocr(monkeypatch):
    setup(monkeypatch);calls=[]
    def predict(*a,**k):calls.append(1);return pix2text_mfr.Prediction("x^2=4",True,"eos",8,.1)
    monkeypatch.setattr(pix2text_mfr,"recognize",predict)
    r=source();scanned_math.route_scanned_math(r,Path("unused"))
    assert len(calls)==1 and len(r.blocks)==2
    assert r.blocks[0].content=="y=2"
    assert r.blocks[1].metadata["source_ocr_blocks"]==["ocr"]
    assert r.blocks[1].requires_review
    md=blocks_to_markdown(r.blocks);assert md.count("x^2=4")==1 and "y=2" not in md


def test_incomplete_prediction_preserves_original(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(pix2text_mfr,"recognize",lambda *a,**k:pix2text_mfr.Prediction("x^2",False,"token_limit",2,.1))
    r=source();scanned_math.route_scanned_math(r,Path("unused"));assert len(r.blocks)==1 and r.blocks[0].content=="y=2"


def test_budget_zero_makes_no_recognition_call(monkeypatch):
    setup(monkeypatch);monkeypatch.setenv("AXTRACT_MFR_PAGE_SECONDS","0")
    monkeypatch.setattr(pix2text_mfr,"recognize",lambda *a,**k:pytest.fail("budget exceeded"))
    r=source();scanned_math.route_scanned_math(r,Path("unused"));assert len(r.blocks)==1


def test_model_failure_preserves_ocr(monkeypatch):
    setup(monkeypatch)
    def broken(*a,**k):raise RuntimeError("model failure")
    monkeypatch.setattr(pix2text_mfr,"recognize",broken)
    r=source();scanned_math.route_scanned_math(r,Path("unused"));assert r.blocks[0].content=="y=2" and len(r.blocks)==1


def test_cleanup_preserves_vector_meaning_and_text_spaces():
    raw=r"\mathbf{x} + \boldsymbol{y} = \text{two words}"
    assert tidy_latex(raw)==raw
    assert r"\mathbf" not in tidy_latex(raw,preserve_semantics=False)


@pytest.mark.parametrize("value",[r"\input{file}",r"\write18{cmd}",r"\href{javascript:x}{x}",r"\def\x{1}",r"\htmlClass{x}{y}",r"\unknown{3}",r"\frac{x}{",r"x^^2"])
def test_extended_validator_still_rejects_unsafe_or_broken(value):
    assert not validate_latex(value).ok


def test_extended_validator_accepts_legitimate_relations_and_delimiters():
    for value in (r"x \therefore y",r"\bigl(x\bigr)",r"x\lesssim y"):
        assert validate_latex(value).ok



def test_low_memory_does_not_load_or_recognize(monkeypatch):
    setup(monkeypatch);monkeypatch.setattr(scanned_math,"available_memory_bytes",lambda:100*1024**2)
    monkeypatch.setattr(pix2text_mfr,"recognize",lambda *a,**k:pytest.fail("low-memory recognition"))
    r=source();scanned_math.route_scanned_math(r,Path("unused"));assert len(r.blocks)==1
    assert r.blocks[0].metadata["scanned_math_page"]["stop_reason"]=="low_memory"



def test_bold_prose_is_not_mathematical_structure():
    assert not scanned_math.math_structure(r"5. \mathbf{Summary} \hspace{1cm} \textbf{Extensions}")
    assert scanned_math.math_structure(r"\mathbf{x}^2=4")


def test_token_spaced_dimensions_are_not_validated_as_latex():
    assert not validate_latex(r"x=1\hspace{1 . 0 c m}").ok
    assert validate_latex(r"x=1\hspace{1.0cm}").ok
