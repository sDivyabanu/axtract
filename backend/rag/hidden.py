"""Hidden-content and active-content detection (nothing is ever executed).

Hidden text is text a human reader cannot see but a text extractor (and therefore an LLM) can:
  PDF   white / near-background fill, font smaller than 1 pt, positioned outside the page, invisible
        render mode (Tr 3). Invisible render mode is also how OCR'd scans carry their searchable text
        layer, so it is only reported on pages that are NOT dominated by a picture.
  DOCX  runs marked hidden (w:vanish), white text, or fonts smaller than 1 pt.
  XLSX  hidden sheets, hidden rows and columns (detected by the extractor, passed in block metadata).

Active content (reported as findings, "not executed"): PDF JavaScript / OpenAction / Launch / embedded
files; Office VBA macros, DDE fields, embedded OLE objects and external relationships (remote templates).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from models.document import DocumentBlock


@dataclass
class HiddenSpan:
    block_id: str | None
    reason: str
    text: str
    page: int | None = None
    bbox: list[float] | None = None


@dataclass
class ActiveFinding:
    reason: str
    detail: str
    action_taken: str = "not_executed"


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _is_near_white(color) -> bool:
    if color is None:
        return False
    try:
        vals = [float(c) for c in (color if isinstance(color, (list, tuple)) else [color])]
    except (TypeError, ValueError):
        return False
    if len(vals) == 1:  # grey
        return vals[0] >= 0.94
    if len(vals) == 3:  # RGB
        return all(v >= 0.94 for v in vals)
    if len(vals) == 4:  # CMYK: white = no ink
        return all(v <= 0.06 for v in vals)
    return False


# ---------------------------------------------------------------------------
# block mapping
# ---------------------------------------------------------------------------


def _blocks_containing(blocks: list[DocumentBlock], text: str, page: int | None) -> list[DocumentBlock]:
    """Blocks whose text is (mostly) this hidden run."""
    t = _norm(text)
    if len(t) < 6:
        return []
    out = []
    for b in blocks:
        if page is not None and b.page != page:
            continue
        c = _norm(b.content or "")
        if not c:
            continue
        if t in c or (c in t and len(c) >= 0.6 * len(t)):
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf_hidden(path: Path, blocks: list[DocumentBlock]) -> list[HiddenSpan]:
    import pdfplumber

    spans: list[HiddenSpan] = []
    picture_pages: set[int] = set()
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            pw, ph = float(page.width), float(page.height)
            covered = sum(max(0.0, i["x1"] - i["x0"]) * max(0.0, i["bottom"] - i["top"]) for i in page.images)
            if covered >= 0.5 * pw * ph:
                picture_pages.add(pno)  # a scan: its invisible text layer is OCR output, not hidden text
            filled = [r for r in page.rects if r.get("fill") and not _is_near_white(r.get("non_stroking_color"))
                      and (r["x1"] - r["x0"]) > 4 and (r["bottom"] - r["top"]) > 4]
            image_boxes = [(i["x0"], i["top"], i["x1"], i["bottom"]) for i in page.images]
            runs: dict[str, list[dict]] = {"hidden_text_white": [], "hidden_text_tiny": [], "hidden_text_offpage": []}
            for ch in page.chars:
                txt = ch.get("text", "")  # spaces are kept so the hidden sentence reads naturally
                cx, cy = (ch["x0"] + ch["x1"]) / 2, (ch["top"] + ch["bottom"]) / 2
                if ch["x1"] < -1 or ch["x0"] > pw + 1 or ch["bottom"] < -1 or ch["top"] > ph + 1:
                    runs["hidden_text_offpage"].append(ch)
                elif float(ch.get("size", 10)) < 1.0:
                    runs["hidden_text_tiny"].append(ch)
                elif _is_near_white(ch.get("non_stroking_color")):
                    on_dark = any(r["x0"] <= cx <= r["x1"] and r["top"] <= cy <= r["bottom"] for r in filled)
                    on_image = any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in image_boxes)
                    if not on_dark and not on_image:
                        runs["hidden_text_white"].append(ch)
            for reason, chars in runs.items():
                if not chars:
                    continue
                # group characters into lines (same baseline) -> readable strings
                chars.sort(key=lambda c: (round(c["top"], 0), c["x0"]))
                line, last_top, lines = [], None, []
                for c in chars:
                    if last_top is not None and abs(c["top"] - last_top) > 3:
                        lines.append(line); line = []
                    line.append(c); last_top = c["top"]
                if line:
                    lines.append(line)
                for ln in lines:
                    text = "".join(c["text"] for c in ln).strip()
                    if len(text) < 3:
                        continue
                    box = [round(min(c["x0"] for c in ln) / pw, 6), round(min(c["top"] for c in ln) / ph, 6),
                           round(max(c["x1"] for c in ln) / pw, 6), round(max(c["bottom"] for c in ln) / ph, 6)]
                    box = [min(1.0, max(0.0, v)) for v in box]
                    matched = _blocks_containing(blocks, text, pno)
                    if matched:
                        for b in matched:
                            spans.append(HiddenSpan(b.id, reason, text, pno, box))
                    else:
                        spans.append(HiddenSpan(None, reason, text, pno, box if box[2] > box[0] and box[3] > box[1] else None))
    spans += _pdf_invisible_mode(path, blocks, picture_pages)
    return spans


def _pdf_invisible_mode(path: Path, blocks: list[DocumentBlock], picture_pages: set[int]) -> list[HiddenSpan]:
    """Text drawn with render mode 3 (invisible), except on picture-dominated pages (OCR text layers)."""
    from pypdf import PdfReader
    from pypdf.generic import ContentStream

    out: list[HiddenSpan] = []
    try:
        reader = PdfReader(str(path))
    except Exception:  # noqa: BLE001
        return out
    for pno, page in enumerate(reader.pages, 1):
        if pno in picture_pages:
            continue  # a scan with a searchable OCR layer
        try:
            contents = page.get_contents()
            if contents is None:
                continue
            stream = ContentStream(contents, reader)
        except Exception:  # noqa: BLE001
            continue
        mode, buf = 0, []
        for operands, op in stream.operations:
            if op == b"Tr" and operands:
                mode = int(operands[0])
            elif op in (b"Tj", b"'", b'"', b"TJ") and mode == 3:
                if op == b"TJ":
                    buf.append("".join(x.decode("latin-1") if isinstance(x, (bytes,)) else str(x)
                                       for x in operands[0] if not isinstance(x, (int, float))))
                else:
                    s = operands[-1]
                    buf.append(s.decode("latin-1") if isinstance(s, bytes) else str(s))
        text = " ".join(buf).strip()
        if len(text) >= 3:
            matched = _blocks_containing(blocks, text, pno)
            if matched:
                out += [HiddenSpan(b.id, "hidden_text_invisible", text, pno, None) for b in matched]
            else:
                out.append(HiddenSpan(None, "hidden_text_invisible", text, pno, None))
    return out


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


def _docx_hidden(path: Path, blocks: list[DocumentBlock]) -> list[HiddenSpan]:
    import docx

    out: list[HiddenSpan] = []
    try:
        doc = docx.Document(str(path))
    except Exception:  # noqa: BLE001
        return out
    for para in doc.paragraphs:
        hidden: list[str] = []
        reason = ""
        for run in para.runs:
            txt = run.text
            if not txt.strip():
                continue
            size = run.font.size.pt if run.font.size else None
            rgb = str(run.font.color.rgb) if run.font.color is not None and run.font.color.type is not None and run.font.color.rgb else ""
            if run.font.hidden:
                reason = "hidden_text_invisible"
            elif rgb.upper() == "FFFFFF":
                reason = "hidden_text_white"
            elif size is not None and size < 1.0:
                reason = "hidden_text_tiny"
            else:
                continue
            hidden.append(txt)
        text = " ".join(hidden).strip()
        if len(text) >= 3:
            matched = _blocks_containing(blocks, text, None)
            out += [HiddenSpan(b.id, reason, text, None, None) for b in matched] or [HiddenSpan(None, reason, text, None, None)]
    return out


# ---------------------------------------------------------------------------
# XLSX (the extractor records hidden sheets / rows / columns in block metadata)
# ---------------------------------------------------------------------------


def _xlsx_hidden(blocks: list[DocumentBlock]) -> list[HiddenSpan]:
    out: list[HiddenSpan] = []
    for b in blocks:
        if b.metadata.get("hidden_sheet"):
            out.append(HiddenSpan(b.id, "hidden_sheet", f"Hidden sheet '{b.metadata.get('sheet_name', '')}': " + " ".join((b.content or "").split())[:240], b.page, None))
        hc = b.metadata.get("hidden_content") or []
        if hc:
            sample = "; ".join(f"{h['cell']}: {h['value']}" for h in hc[:6])
            # the hidden values were already removed from the table, so this span is informational (block_id=None)
            out.append(HiddenSpan(None, "hidden_row_col", sample, b.page, None))
    return out


def scan(path: Path | None, file_type: str, blocks: list[DocumentBlock]) -> list[HiddenSpan]:
    """Hidden text found in a document. Spans with a block_id cause that block to be quarantined."""
    spans: list[HiddenSpan] = []
    try:
        if file_type == "pdf" and path is not None:
            spans = _pdf_hidden(path, blocks)
        elif file_type == "docx" and path is not None:
            spans = _docx_hidden(path, blocks)
        elif file_type == "xlsx":
            spans = _xlsx_hidden(blocks)
    except Exception:  # noqa: BLE001 - a scanner failure must never block ingestion
        return spans
    return spans


# ---------------------------------------------------------------------------
# active content
# ---------------------------------------------------------------------------


def active_findings(path: Path | None, file_type: str) -> list[ActiveFinding]:
    out: list[ActiveFinding] = []
    if path is None:
        return out
    try:
        if file_type == "pdf":
            out = _pdf_active(path)
        elif file_type in ("docx", "pptx", "xlsx"):
            out = _office_active(path)
    except Exception:  # noqa: BLE001
        pass
    return out


def _pdf_active(path: Path) -> list[ActiveFinding]:
    from pypdf import PdfReader

    out: list[ActiveFinding] = []
    r = PdfReader(str(path))
    root = r.trailer["/Root"]
    if "/OpenAction" in root:
        out.append(ActiveFinding("pdf_open_action", "The document runs an action when opened (/OpenAction)."))
    if "/AA" in root:
        out.append(ActiveFinding("pdf_additional_actions", "The document defines additional actions (/AA)."))
    names = root.get("/Names")
    if names is not None:
        names = names.get_object()
        if "/JavaScript" in names:
            out.append(ActiveFinding("pdf_javascript", "The document contains JavaScript (/Names /JavaScript)."))
        if "/EmbeddedFiles" in names:
            out.append(ActiveFinding("pdf_embedded_file", "The document embeds other files (/EmbeddedFiles)."))
    for pno, page in enumerate(r.pages, 1):
        if "/AA" in page:
            out.append(ActiveFinding("pdf_additional_actions", f"Page {pno} defines additional actions (/AA)."))
        for annot in page.get("/Annots") or []:
            a = annot.get_object()
            act = a.get("/A")
            if act is not None:
                s = str(act.get_object().get("/S", ""))
                if s in ("/Launch", "/JavaScript", "/SubmitForm", "/ImportData"):
                    out.append(ActiveFinding("pdf_" + s.strip("/").lower(), f"Page {pno} has a {s[1:]} action on an annotation."))
    seen, uniq = set(), []
    for f in out:
        if (f.reason, f.detail) not in seen:
            seen.add((f.reason, f.detail)); uniq.append(f)
    return uniq


_DDE = re.compile(rb"\bDDE(?:AUTO)?\b", re.I)


def _office_active(path: Path) -> list[ActiveFinding]:
    out: list[ActiveFinding] = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if any(n.lower().endswith("vbaproject.bin") for n in names):
            out.append(ActiveFinding("office_macro", "The file contains a VBA macro project (vbaProject.bin)."))
        embeds = [n for n in names if "/embeddings/" in n.lower() or "oleobject" in n.lower()]
        if embeds:
            out.append(ActiveFinding("office_ole_object", f"The file embeds {len(embeds)} OLE object(s)."))
        for n in names:
            low = n.lower()
            if low.endswith(".rels"):
                data = z.read(n)
                for m in re.finditer(rb'Target="([^"]+)"[^>]*TargetMode="External"|TargetMode="External"[^>]*Target="([^"]+)"', data):
                    target = (m.group(1) or m.group(2) or b"").decode("utf-8", "replace")
                    kind = "office_remote_template" if b"attachedTemplate" in data else "office_external_link"
                    out.append(ActiveFinding(kind, f"External reference (not fetched): {target[:120]}"))
            elif low.endswith(".xml") and ("document" in low or "sheet" in low or "slide" in low):
                if _DDE.search(z.read(n)):
                    out.append(ActiveFinding("office_dde", f"A DDE field instruction was found in {n}."))
    seen, uniq = set(), []
    for f in out:
        if (f.reason, f.detail) not in seen:
            seen.add((f.reason, f.detail)); uniq.append(f)
    return uniq
