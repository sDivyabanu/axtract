"""Opt-in, bounded scanned-line math recognition. Original OCR stays auditable."""
from __future__ import annotations
import logging
import re
import os
import time
from pathlib import Path
from models.document import BlockType
from models.errors import AppError
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
    from services.pdf_router import fit_zoom
    return 72.0 * fit_zoom(width_pt, height_pt, base_dpi=base)


def page_image(path, page):
    """Page image for line recognition, rendered under the OCR pixel/side caps.

    The OCR stage's cached image is deliberately not reused: it comes from a different renderer, and
    measured on the fixed scanned-math sample the pixel differences move line bands enough to lose
    assertions (see evaluation/test_integration_interactions.py). Memory stays bounded by the caps."""
    from services.preview_service import render_page_pil
    size = page_size_points(path, page)
    dpi = render_dpi_for(*size) if size else 100
    return render_page_pil(path, page, dpi)


# A line of plain words with no mathematical signal at all is text, however short ("Dear Sir:-", "I am,"): the recognizer
# would only turn it into a garbage equation. Digits, relations, operators, brackets and Greek letters count as signal, and
# so do function names, so "sin x" is still offered to the recognizer.
_MATH_SIGNAL=re.compile(r"[=+*/^_<>\\{}\[\]|\u2264\u2265\u2260\u2248\u00b1\u00d7\u00f7\u222b\u2211\u221a\u221e\u2202\u2206\u2192\u2190]|\d|[\u0370-\u03ff]")
_FUNCTION_WORDS=frozenset("sin cos tan cot sec csc sinh cosh tanh log ln exp lim max min det dim sup inf arg mod gcd lcm".split())


_BARE_NUMBER = re.compile(r"^[\s\-–—.,:;()\[\]{}*|•·]*\d{1,4}[\s\-–—.,:;()\[\]{}*|•·]*$")  # decoration only, never an operator or symbol


def bare_number_line(text):
    """A page number, margin line number or equation tag on its own ("36", "- 36 -", "(12)") is not a formula; offering it
    to the recognizer produced a bogus equation that hid the real page number and disturbed the page's column layout."""
    return bool(_BARE_NUMBER.match((text or "").strip()))


def running_head_line(text):
    """Page headers such as "AFFINE TRANSFORMATIONS 29" or "56 SIMPLE AREAS AND VOLUMES": capitals only, never a formula."""
    words=re.findall(r"[A-Za-z]+",text or "")
    if len(words)<2 or not all(w.isupper() for w in words) or max(len(w) for w in words)<4:
        return False
    return not re.search(r"[=+^_<>\|]",text)


def salvage_complete_rows(latex):
    """Keep the finished rows of an array/aligned block whose decoding was cut off (token or time limit).

    A tall block of equations is decoded row by row, so everything before the last row break is complete. The cut-off row is
    dropped and the environment is closed; anything that is not such a block, or does not stay brace-balanced, is not salvaged."""
    match=re.match(r"\s*\\begin\{(array|aligned|cases|align)\}",latex)
    if not match:
        return ""
    cut=latex.rfind("\\\\")
    if cut<=match.end():
        return ""
    body=latex[:cut].rstrip()
    if body.count("{")!=body.count("}"):
        return ""
    return body+" \\\\ \\end{"+match.group(1)+"}"


def math_priority(text,box):
    """Higher first: lines that look like display equations get the page's recognition budget before ordinary lines do."""
    t=text or ""
    signal=len(re.findall(r"[=+^_/<>\|≤≥≠∫∑√]",t))+0.4*len(re.findall(r"\d",t))
    narrow=1.0 if (box[2]-box[0])<0.75 else 0.0   # a display equation does not span the full measure
    centred=1.0 if box[0]>0.12 and (box[2]-box[0])<0.75 else 0.0
    return signal/max(10,len(t))*4+narrow*0.5+centred*0.5


def plain_text_line(text):
    t=(text or "").strip()
    if not t or _MATH_SIGNAL.search(t):
        return False
    words=[w.lower() for w in re.findall(r"[A-Za-z]{3,}",t)]
    return bool(words) and not any(w in _FUNCTION_WORDS for w in words)


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
        if looks_like_prose(text) or plain_text_line(text) or bare_number_line(text) or running_head_line(text):
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


_warned_unavailable=False


def _warn_unavailable_once(status):
    """AXTRACT_SCANNED_MATH is on but the recognizer cannot load: say why, once per process (never download anything)."""
    global _warned_unavailable
    if _warned_unavailable:
        return
    _warned_unavailable=True
    logger.warning("Scanned math is enabled but Pix2Text-MFR is unavailable (%s). Install the pinned packages from "
                   "backend/requirements.txt and run `python scripts/scanned_math_assets.py verify`; see docs/SCANNED_MATH_SETUP.md.",status)


_MATHY_TOKEN=re.compile(r"^[\(\[]*[A-Za-z]?[\d.,^\-+*/=<>()\[\]|−—–±²³½]*[A-Za-z]{0,2}[\d,.;:)\]]*$")
_FUNCTION_TOKENS=frozenset(("sin","cos","tan","cot","sec","csc","log","ln","exp"))


def prose_math_run(tokens):
    """(first, last) token index of the longest run of formula-like tokens around an "=" in a line of text, or None.

    A prose line such as "revolving about the line y = x the part of" is read as words, so the equation inside it never reaches
    the formula recognizer. Tokens of a formula are short, made of one or two letters, digits and operators; ordinary words
    (three or more letters, other than function names) end the run."""
    def formula_like(token):
        bare=token.strip(".,;:()")
        if bare.lower() in _FUNCTION_TOKENS:
            return True
        return bool(_MATHY_TOKEN.match(token)) and not re.fullmatch(r"[A-Za-z]{3,}",bare)
    best=None
    for i,token in enumerate(tokens):
        if "=" not in token:
            continue
        lo=hi=i
        while lo>0 and formula_like(tokens[lo-1]):
            lo-=1
        while hi+1<len(tokens) and formula_like(tokens[hi+1]):
            hi+=1
        if best is None or hi-lo>best[1]-best[0]:
            best=(lo,hi)
    return best


def _prose_equations(result,page,image,native_words,stats,budget,started,max_attempts=10):
    """Read formulas embedded in prose lines (located by the scan's own word boxes) with the same recognizer and checks.

    The prose line itself is left untouched; the recognised formula is added as an equation block beside it."""
    lines={}
    for word in native_words:
        lines.setdefault((word[5],word[6]),[]).append(word)
    width,height=image.size
    attempts=0
    for _,words in sorted(lines.items(),key=lambda item:(min(w[1] for w in item[1]),min(w[0] for w in item[1]))):
        words.sort(key=lambda w:w[0])
        text=" ".join(w[4] for w in words)
        run=prose_math_run([w[4] for w in words])
        if run is None or run[1]-run[0]>14:
            continue
        ram=available_memory_bytes()
        available=min(budget-(time.monotonic()-started),deadline.remaining()-6)
        if (ram is not None and ram<300*1024**2) or available<=0 or attempts>=max_attempts:
            stats["stop_reason"]="budget_or_line_limit" if attempts>=max_attempts or available<=0 else "low_memory"
            return
        chosen=words[run[0]:run[1]+1]
        box=(max(0,min(w[0] for w in chosen)-.006),max(0,min(w[1] for w in words)-.004),
             min(1,max(w[2] for w in chosen)+.006),min(1,max(w[3] for w in words)+.004))
        if any(b.page==page and b.type==BlockType.EQUATION and b.bbox and _overlap(box,b.bbox)>.5 for b in result.blocks):
            continue
        attempts+=1;stats["attempted"]+=1
        crop=image.crop((int(box[0]*width),int(box[1]*height),int(box[2]*width),int(box[3]*height)))
        prediction=pix2text_mfr.recognize(crop,max_seconds=min(5,available))
        latex=tidy_latex(prediction.latex,preserve_semantics=True)
        if not prediction.completed or not validate_latex(latex).ok or not math_structure(latex):
            stats["rejected"].append({"bbox":box,"reason":"prose_line_"+(prediction.stop_reason if not prediction.completed else "validation_or_not_math")})
            continue
        equation_id=f"p{page}-mfr{stats['accepted']}"
        result.blocks.append(make_equation_block(equation_id,page,box,latex,"pix2text_mfr_onnx",.45,
            ["unverified_by_independent_math_reader"],extractor="pix2text_mfr_onnx",
            extra_metadata={"route":"scanned","prose_line_formula":True,"crop_ref":{"page":page,"bbox":list(box)},
                "source_ocr_text":" ".join(w[4] for w in chosen),"model_stop_reason":prediction.stop_reason,
                "model_tokens":prediction.tokens,"recognition_seconds":prediction.seconds,
                "confidence_source":"model_prediction_requires_review"}))
        stats["accepted"]+=1


def route_scanned_math(result,path: Path):
    mode=os.environ.get("AXTRACT_SCANNED_MATH","0").strip().lower()
    if mode not in ("1","true","on","require"):
        return
    strict=mode=="require"  # benchmark/reproduction mode: a missing package or model is an error, never a silent fallback
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
            stats["stop_reason"]="model_unavailable"
            stats["setup"]={k:v for k,v in status.items() if k!="available"}
            if strict:
                raise AppError("SCANNED_MATH_UNAVAILABLE",
                               f"AXTRACT_SCANNED_MATH=require but Pix2Text-MFR cannot load ({stats['setup']}). Install the pinned "
                               "packages, run `python scripts/scanned_math_assets.py check`, and see docs/SCANNED_MATH_SETUP.md.",
                               status_code=503)
            _warn_unavailable_once(status)
            continue
        ram=available_memory_bytes()
        if ram is not None and ram<650*1024**2:
            stats["stop_reason"]="low_memory";continue
        budget=min(max(0,float(os.environ.get("AXTRACT_MFR_PAGE_SECONDS","28"))),deadline.remaining()-6)
        limit=max(0,min(64,int(os.environ.get("AXTRACT_MFR_MAX_LINES","24"))))
        if budget<=0 or limit==0:
            stats["stop_reason"]="budget";continue
        started=time.monotonic()
        try:
            image=page_image(path,page)
            ordered=sorted(candidates(image,originals,page,native_words=native.get(page)),key=lambda item:-math_priority(item[1],item[0]))
            for box,ocr,covered in ordered:
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
                salvaged=False
                if not prediction.completed:
                    kept=salvage_complete_rows(latex)
                    if kept and validate_latex(kept).ok and math_structure(kept):
                        latex,salvaged=kept,True
                if (not prediction.completed and not salvaged) or not validate_latex(latex).ok or not math_structure(latex):
                    stats["rejected"].append({"bbox":box,"reason":prediction.stop_reason if not prediction.completed else "validation_or_not_math"})
                    continue
                if any(b.page==page and b.type==BlockType.EQUATION and b.bbox and not b.metadata.get("inline") and _overlap(box,b.bbox)>.5 for b in result.blocks):
                    continue
                equation_id=f"p{page}-mfr{stats['accepted']}"
                equation=make_equation_block(equation_id,page,box,latex,"pix2text_mfr_onnx",.45,
                    ["unverified_by_independent_math_reader"],extractor="pix2text_mfr_onnx",
                    extra_metadata={"route":"scanned","crop_ref":{"page":page,"bbox":list(box)},
                        "source_ocr_blocks":[b.id for b in covered],"source_ocr_text":ocr,
                        "model_stop_reason":prediction.stop_reason,"truncated_rows_dropped":salvaged,"model_tokens":prediction.tokens,
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
            if page in native and not stats.get("stop_reason"):
                _prose_equations(result,page,image,native[page],stats,budget,started)
        except Exception as exc:
            stats["stop_reason"]=f"safe_fallback:{type(exc).__name__}"
            logger.warning("Scanned math fallback on page %s: %s",page,exc)
        stats["seconds"]=time.monotonic()-started
