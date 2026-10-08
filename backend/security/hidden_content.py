# security/hidden_content.py
"""
Detects content that is invisible to human readers but readable by a parser.
Covers:
  PDF  : white/near-white text, sub-1pt font, off-page bbox, text under images
  XLSX : hidden sheets, hidden rows, hidden columns
  PPTX : zero-size / off-slide textboxes

All hidden content goes into hidden_content[] — NEVER into the main body.
Prompt injection is scanned on hidden content too (higher priority there).
"""
import re
from typing import Optional
from security.unicode_guard import scan_injection


# ── thresholds what────────────────────────────────────────────────────────────────

MIN_FONT_SIZE   = 1.0    # pt — below this = invisible
WHITE_THRESHOLD = 0.9    # RGB channel average — above this = near-white
OFF_PAGE_MARGIN = 0.01   # normalised — outside page + this margin = off-page


# ── finding builder ───────────────────────────────────────────────────────────

def _finding(ftype: str, severity: str, detail: str,
             block_id: str = "", page: int = None) -> dict:
    f = {"type": ftype, "severity": severity,
         "detail": detail, "block_id": block_id,
         "action_taken": "moved_to_hidden_content"}
    if page is not None:
        f["page"] = page
    return f


# ── colour helper ─────────────────────────────────────────────────────────────

# REPLACE _is_near_white in security/hidden_content.py

def _is_near_white(color) -> bool:
    """
    color from pymupdf span is a packed int 0xRRGGBB.
    White = 16777215 (0xFFFFFF).
    Also handles tuple (r,g,b) floats 0-1.
    """
    if color is None:
        return False
    if isinstance(color, int):
        r = ((color >> 16) & 0xFF) / 255
        g = ((color >>  8) & 0xFF) / 255
        b = (color         & 0xFF) / 255
        return r >= WHITE_THRESHOLD and g >= WHITE_THRESHOLD and b >= WHITE_THRESHOLD
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        return all(c >= WHITE_THRESHOLD for c in color[:3])
    return False


# ══════════════════════════════════════════════════════════════════════════════
# PDF hidden content
# ══════════════════════════════════════════════════════════════════════════════

def scan_pdf_page(page, page_num: int = 0) -> dict:
    """
    Scan one PyMuPDF page for hidden text spans.

    page: fitz.Page object
    Returns:
        {
          "visible":        [span_dict, ...]   <- safe to emit
          "hidden_content": [hidden_item, ...] <- separated, never in main body
          "findings":       [finding_dict, ...]
        }
    """
    try:
        import fitz
    except ImportError:
        return {"visible": [], "hidden_content": [], "findings": [],
                "error": "pymupdf not installed"}

    visible        = []
    hidden_content = []
    findings       = []

    page_rect = page.rect                        # page bounding box
    # get image bboxes on this page (for under-image detection)
    image_bboxes = [fitz.Rect(img["bbox"])
                    for img in page.get_image_info(xrefs=True)
                    if "bbox" in img]
    blocks = page.get_text("dict")
    for block in blocks.get("blocks", []):
        if block.get("type") != 0:               # 0 = text block
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text  = span.get("text", "").strip()
                if not text:
                    continue

                color     = span.get("color", 0)
                font_size = span.get("size", 12)
                origin    = span.get("origin", (0, 0))
                bbox      = fitz.Rect(span.get("bbox", [0,0,0,0]))
                flags_val = span.get("flags", 0)

                hidden_reason = None

                # 1. near-white text
                if _is_near_white(color):
                    hidden_reason = "hidden_text_white"

                # 2. sub-1pt font (invisible)
                elif font_size < MIN_FONT_SIZE:
                    hidden_reason = "hidden_text_tiny_font"

                # 3. render mode 3 = invisible
                elif (flags_val & 0b111) == 3:
                    hidden_reason = "hidden_text_invisible_render"

                # 4. off-page bbox
                elif (bbox.x1 < -OFF_PAGE_MARGIN or
                      bbox.y1 < -OFF_PAGE_MARGIN or
                      bbox.x0 > page_rect.width  + OFF_PAGE_MARGIN or
                      bbox.y0 > page_rect.height + OFF_PAGE_MARGIN):
                    hidden_reason = "hidden_text_offpage"

                # 5. text fully covered by an image
                else:
                    for img_rect in image_bboxes:
                        if img_rect.contains(bbox):
                            hidden_reason = "hidden_text_under_image"
                            break

                item = {
                    "text":      text,
                    "page":      page_num,
                    "font_size": font_size,
                    "color":     color,
                    "bbox":      list(bbox),
                    "reason":    hidden_reason or "visible",
                }

                if hidden_reason:
                    hidden_content.append(item)
                    findings.append(_finding(
                        hidden_reason, "high",
                        f"font={font_size}pt color={color!r} "
                        f"text='{text[:60]}'",
                        page=page_num))
                    # scan hidden text for injection — higher risk
                    inj = scan_injection(text, f"hidden_p{page_num}")
                    if inj:
                        findings.append(inj)
                else:
                    visible.append(item)

    return {"visible": visible,
            "hidden_content": hidden_content,
            "findings": findings}


# ══════════════════════════════════════════════════════════════════════════════
# XLSX hidden content
# ══════════════════════════════════════════════════════════════════════════════

def scan_xlsx(path: str) -> dict:
    """
    Scan an xlsx workbook for hidden sheets, rows, columns.
    Returns findings + a list of hidden cell values.
    """
    try:
        import openpyxl
    except ImportError:
        return {"hidden_content": [], "findings": [],
                "error": "openpyxl not installed"}

    findings       = []
    hidden_content = []

    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)

    for sheet_name in wb.sheetnames:
        ws    = wb[sheet_name]
        state = ws.sheet_state          # "visible" | "hidden" | "veryHidden"

        if state != "visible":
            findings.append(_finding(
                "hidden_sheet", "high",
                f"sheet '{sheet_name}' state='{state}'",
                block_id=sheet_name))
            # extract hidden sheet content for inspection
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None:
                        text = str(cell)
                        hidden_content.append({
                            "sheet": sheet_name,
                            "value": text,
                            "reason": "hidden_sheet"
                        })
                        inj = scan_injection(text, f"hidden_sheet:{sheet_name}")
                        if inj:
                            findings.append(inj)
            continue

        # visible sheet — check hidden rows
        hidden_rows = []
        for row_dim in ws.row_dimensions.values():
            if row_dim.hidden:
                hidden_rows.append(row_dim.index)
        if hidden_rows:
            findings.append(_finding(
                "hidden_rows_columns", "medium",
                f"sheet '{sheet_name}': {len(hidden_rows)} hidden row(s) "
                f"e.g. row {hidden_rows[0]}",
                block_id=sheet_name))

        # check hidden columns
        hidden_cols = []
        for col_dim in ws.column_dimensions.values():
            if col_dim.hidden:
                hidden_cols.append(col_dim.index)
        if hidden_cols:
            findings.append(_finding(
                "hidden_rows_columns", "medium",
                f"sheet '{sheet_name}': {len(hidden_cols)} hidden col(s) "
                f"e.g. col {hidden_cols[0]}",
                block_id=sheet_name))

    wb.close()
    return {"hidden_content": hidden_content, "findings": findings}


# ══════════════════════════════════════════════════════════════════════════════
# PPTX hidden content
# ══════════════════════════════════════════════════════════════════════════════

def scan_pptx(path: str) -> dict:
    """
    Scan PPTX for zero-size or off-slide textboxes.
    """
    try:
        from pptx import Presentation
        from pptx.util import Emu
    except ImportError:
        return {"hidden_content": [], "findings": [],
                "error": "python-pptx not installed"}

    findings       = []
    hidden_content = []

    prs = Presentation(path)
    for slide_num, slide in enumerate(prs.slides):
        slide_w = prs.slide_width
        slide_h = prs.slide_height

        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = " ".join(
                p.text for p in shape.text_frame.paragraphs).strip()
            if not text:
                continue

            left, top  = shape.left,  shape.top
            width, height = shape.width, shape.height
            hidden_reason = None

            # zero or negative size
            if width <= 0 or height <= 0:
                hidden_reason = "hidden_text_tiny_font"

            # off-slide (outside slide bounds with margin)
            elif (left + width < 0 or top + height < 0 or
                  left > slide_w or top > slide_h):
                hidden_reason = "hidden_text_offpage"

            if hidden_reason:
                hidden_content.append({
                    "slide": slide_num + 1,
                    "text":  text,
                    "reason": hidden_reason
                })
                findings.append(_finding(
                    hidden_reason, "high",
                    f"slide {slide_num+1} shape '{shape.name}' "
                    f"size=({width},{height}) pos=({left},{top}) "
                    f"text='{text[:60]}'",
                    block_id=shape.name))
                inj = scan_injection(text, f"pptx_s{slide_num+1}")
                if inj:
                    findings.append(inj)

    return {"hidden_content": hidden_content, "findings": findings}