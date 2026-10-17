"""Opt-in, bounded scanned-line math recognition. Original OCR stays auditable."""
from __future__ import annotations
import logging
import re
import os
import time
from pathlib import Path
from models.document import BlockType
from services.equation_service import make_equation_block
from services.vision import pix2text_mfr
from services.vision.line_math import segment_lines,looks_like_prose,tidy_latex
from services.vision.latex import validate_latex,looks_mathy
from utils import deadline

logger=logging.getLogger(__name__)


def available_memory_bytes():
    """Best-effort local RAM guard; no dependency or model initialization."""
    try:
        if os.name == "nt":
            import ctypes
            class Memory(ctypes.Structure):
                _fields_=[("length",ctypes.c_ulong),("load",ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ("total","available","total_page","avail_page","total_virtual","avail_virtual","extended")]
            mem=Memory();mem.length=ctypes.sizeof(mem)
            return mem.available if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)) else None
        return os.sysconf("SC_AVPHYS_PAGES")*os.sysconf("SC_PAGE_SIZE")
    except (OSError,ValueError,AttributeError):
        return None


def page_size_points(path, page):
    """(width, height) of a PDF page in points, or None when it cannot be read."""
    try:
        import pymupdf
        with pymupdf.open(path) as document:
            rect = document[page - 1].rect
            return float(rect.width), float(rect.height)
    except Exception:  # noqa: BLE001 - sizing is best effort; the caller falls back to a small DPI
        return None


def render_dpi_for(width_pt, height_pt, base=200):
    """Render DPI under the same pixel and side caps the OCR path uses (see pdf_router), so enabling
    scanned math can never reintroduce the unbounded allocations that caused the 60 s timeouts."""
    from services.pdf_router import fit_dpi
    return fit_dpi(width_pt, height_pt, base_dpi=base)


def page_image(path, page):
    """Page image for line recognition, rendered under the OCR pixel/side caps.

    The OCR stage's cached image is deliberately not reused: it comes from a different renderer, and
    measured on the fixed scanned-math sample the pixel differences move line bands enough to lose
    assertions (see evaluation/test_integration_interactions.py). Memory stays bounded by the caps."""
    from services.preview_service import render_page_pil
    size = page_size_points(path, page)
    dpi = render_dpi_for(*size) if size else 100
    return render_page_pil(path, page, dpi)


def _overlap(a,b):
    area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    small=min(max(0,a[2]-a[0])*max(0,a[3]-a[1]),max(0,b[2]-b[0])*max(0,b[3]-b[1]))
    return area/small if small else 0


def candidates(image,blocks,page,*,native_words=None):
    width,height=image.size
    for x0,y0,x1,y1 in segment_lines(image):
        box=(max(0,(x0-6)/width),max(0,(y0-6)/height),min(1,(x1+7)/width),min(1,(y1+7)/height))
        if box[3]-box[1]>.18:
            continue
        covered=[b for b in blocks if b.page==page and b.bbox and (b.extractor=="rapidocr" or (native_words is not None and b.extractor=="pymupdf"))
                 and _overlap(box,b.bbox)>.35]
        text=" ".join(b.content for b in covered)
        if native_words is not None:
            text=" ".join(w[4] for w in native_words if box[0]<=(w[0]+w[2])/2<=box[2] and box[1]<=(w[1]+w[3])/2<=box[3])
        if looks_like_prose(text):
            continue
        if any(b.page==page and b.bbox and b.type in (BlockType.TABLE,BlockType.CHART,BlockType.EQUATION)
               and not (b.type==BlockType.EQUATION and b.metadata.get("inline"))
               and _overlap(box,b.bbox)>.35 for b in blocks):
            continue
        yield box,text,covered


def math_structure(latex):
    # Font, spacing and textual commands alone do not make prose mathematics.
    return bool(re.search(r"[=^_]|\\(?:frac|dfrac|tfrac|sqrt|sum|int|iint|prod|lim|alpha|beta|gamma|delta|theta|lambda|mu|pi|rho|sigma|phi|psi|omega|leq|geq|lt|gt|infty)(?![A-Za-z])",latex))


def hidden_scan_words(path,blocks):
    """Identify image-backed invisible OCR, never ordinary visible native math."""
    import pymupdf
    found={}
    with pymupdf.open(path) as document:
        for number,page in enumerate(document,1):
            if any(b.page==number and b.type==BlockType.TABLE for b in blocks):
                continue  # native table ownership remains authoritative
            log=page.get_bboxlog()
            invisible=sum(item[0]=="ignore-text" for item in log)
            visible=sum(item[0] in ("fill-text","stroke-text") for item in log)
            if not invisible or visible:
                continue
            area=max(1,page.rect.get_area())
            if not any((pymupdf.Rect(info["bbox"]) & page.rect).get_area()/area>=.85 for info in page.get_image_info()):
                continue
            pw,ph=page.rect.width,page.rect.height
            found[number]=[(w[0]/pw,w[1]/ph,w[2]/pw,w[3]/ph,*w[4:]) for w in page.get_text("words")]
    return found


def route_scanned_math(result,path: Path):
    if os.environ.get("AXTRACT_SCANNED_MATH","0").strip().lower() not in ("1","true","on"):
        return
    native=hidden_scan_words(path,result.blocks)
    pages=sorted({b.page for b in result.blocks if b.metadata.get("route") in ("scanned","mixed-ocr")} | set(native))
    if not pages:
        return
    status=pix2text_mfr.mfr_status()
    for page in pages:
        originals=[b for b in result.blocks if b.page==page]
        stats={"enabled":True,"attempted":0,"accepted":0,"rejected":[],"model_available":status["available"]}
        if originals:
            originals[0].metadata["scanned_math_page"]=stats
        if not status["available"]:
            stats["stop_reason"]="model_unavailable";continue
        ram=available_memory_bytes()
        if ram is not None and ram<650*1024**2:
            stats["stop_reason"]="low_memory";continue
        budget=min(max(0,float(os.environ.get("AXTRACT_MFR_PAGE_SECONDS","20"))),deadline.remaining()-6)
        limit=max(0,min(64,int(os.environ.get("AXTRACT_MFR_MAX_LINES","24"))))
        if budget<=0 or limit==0:
            stats["stop_reason"]="budget";continue
        started=time.monotonic()
        try:
            image=page_image(path,page)
            for box,ocr,covered in candidates(image,originals,page,native_words=native.get(page)):
                ram=available_memory_bytes()
                if ram is not None and ram<300*1024**2:
                    stats["stop_reason"]="low_memory";break
                available=min(budget-(time.monotonic()-started),deadline.remaining()-6)
                if available<=0 or stats["attempted"]>=limit:
                    stats["stop_reason"]="budget_or_line_limit";break
                if any(b.page==page and b.type==BlockType.EQUATION and b.bbox and not b.metadata.get("inline") and _overlap(box,b.bbox)>.5 for b in result.blocks):
                    continue
                stats["attempted"]+=1
                width,height=image.size
                crop=image.crop((int(box[0]*width),int(box[1]*height),int(box[2]*width),int(box[3]*height)))
                prediction=pix2text_mfr.recognize(crop,max_seconds=min(5,available))
                latex=tidy_latex(prediction.latex,preserve_semantics=True)
                if not prediction.completed or not validate_latex(latex).ok or not math_structure(latex):
                    stats["rejected"].append({"bbox":box,"reason":prediction.stop_reason if not prediction.completed else "validation_or_not_math"})
                    continue
                if any(b.page==page and b.type==BlockType.EQUATION and b.bbox and not b.metadata.get("inline") and _overlap(box,b.bbox)>.5 for b in result.blocks):
                    continue
                equation_id=f"p{page}-mfr{stats['accepted']}"
                equation=make_equation_block(equation_id,page,box,latex,"pix2text_mfr_onnx",.45,
                    ["unverified_by_independent_math_reader"],extractor="pix2text_mfr_onnx",
                    extra_metadata={"route":"scanned","crop_ref":{"page":page,"bbox":list(box)},
                        "source_ocr_blocks":[b.id for b in covered],"source_ocr_text":ocr,
                        "model_stop_reason":prediction.stop_reason,"model_tokens":prediction.tokens,
                        "recognition_seconds":prediction.seconds,"confidence_source":"model_prediction_requires_review"})
                result.blocks.append(equation);stats["accepted"]+=1
                # Hide only fully covered OCR fragments from Markdown. Their exact
                # original contents/bboxes remain structured and feed Verify.
                for block in covered:
                    b=block.bbox
                    if (not looks_like_prose(block.content) and b[0]>=box[0]-.003 and b[2]<=box[2]+.003
                            and b[1]>=box[1]-.003 and b[3]<=box[3]+.003):
                        block.metadata["math_replaced_in_markdown"]=[equation_id]
                        if page in native:
                            block.metadata["invisible_ocr_source"]=True
        except Exception as exc:
            stats["stop_reason"]=f"safe_fallback:{type(exc).__name__}"
            logger.warning("Scanned math fallback on page %s: %s",page,exc)
        stats["seconds"]=time.monotonic()-started
