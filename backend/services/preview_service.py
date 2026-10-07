"""Page previews: raster page images the viewer draws block boxes on.

Every format is turned into something pypdfium2 can render:
  * PDF            -> rendered directly
  * DOCX/PPTX/XLSX -> converted to PDF with headless LibreOffice (preview + bbox only;
                      text extraction stays native), then rendered
  * JPG/PNG        -> served as a single page

Per-document artifacts live in uploads/previews/<document_id>/ and are removed after a
TTL. LibreOffice runs with a throw-away profile directory, a hard timeout, no network
access needed, and never executes macros (headless conversion with macros disabled).

Coordinates: pypdfium2 reports PDF user space (points, origin bottom-left). Everything
leaving this module is normalized 0-1 with origin top-left, like the block bboxes.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium

from models.document import BBox, BlockType, DocumentBlock
from utils import deadline
from utils.files import UPLOAD_DIR

logger = logging.getLogger(__name__)

PREVIEW_ROOT = UPLOAD_DIR / "previews"
PREVIEW_TTL_SECONDS = 60 * 60
OFFICE_TYPES = {"docx", "pptx", "xlsx"}
IMAGE_TYPES = {"jpg", "jpeg", "png"}
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_DPI = 150
MAX_IMAGE_SIDE = 2400

# pdfium is not thread-safe: serialize every call into it.
_pdfium_lock = threading.RLock()


class PreviewError(Exception):
    """Preview could not be produced. Never fatal for the parse itself."""


@dataclass
class PreviewInfo:
    available: bool = False
    pages: int = 0
    error: str | None = None
    pdf_path: Path | None = None  # PDF used for rendering/locating (None for images)


# ---------------------------------------------------------------------------
# LibreOffice
# ---------------------------------------------------------------------------

_SOFFICE_CANDIDATES = (
    "soffice",
    "libreoffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
    "/usr/bin/soffice",
    "/opt/libreoffice/program/soffice",
)


def find_soffice() -> str | None:
    for cand in _SOFFICE_CANDIDATES:
        found = shutil.which(cand) if "/" not in cand else (cand if Path(cand).exists() else None)
        if found:
            return found
    return None


def office_to_pdf(src: Path, out_dir: Path, timeout: float = 40.0) -> Path:
    """Convert an Office file to PDF with headless LibreOffice (preview/bbox only)."""
    soffice = find_soffice()
    if soffice is None:
        raise PreviewError(
            "LibreOffice (soffice) is not installed; Office previews need it. "
            "Install LibreOffice or set it on PATH."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        cmd = [
            soffice,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(src),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=timeout, text=True, stdin=subprocess.DEVNULL
            )
        except subprocess.TimeoutExpired as exc:
            raise PreviewError(f"LibreOffice conversion timed out after {int(timeout)} s") from exc
    pdf = out_dir / f"{src.stem}.pdf"
    if proc.returncode != 0 or not pdf.exists():
        raise PreviewError(
            f"LibreOffice could not convert the file (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout).strip()[:200]}"
        )
    return pdf


# ---------------------------------------------------------------------------
# Per-document store
# ---------------------------------------------------------------------------


def _doc_dir(document_id: str) -> Path:
    if not _ID_RE.match(document_id):
        raise PreviewError("Invalid document id.")
    return PREVIEW_ROOT / document_id


def cleanup_old(ttl: float = PREVIEW_TTL_SECONDS) -> None:
    if not PREVIEW_ROOT.exists():
        return
    cutoff = time.time() - ttl
    for d in PREVIEW_ROOT.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def prepare(document_id: str, src: Path, file_type: str) -> PreviewInfo:
    """Create the preview artifacts for a freshly uploaded file. Never raises."""
    try:
        cleanup_old()
        d = _doc_dir(document_id)
        d.mkdir(parents=True, exist_ok=True)

        if file_type in IMAGE_TYPES:
            shutil.copyfile(src, d / f"source.{file_type}")
            return PreviewInfo(available=True, pages=1)

        if file_type == "pdf":
            pdf_path = d / "render.pdf"
            shutil.copyfile(src, pdf_path)
        elif file_type in OFFICE_TYPES:
            deadline.check()
            work = d / "src"
            work.mkdir(exist_ok=True)
            shutil.copyfile(src, work / f"source.{file_type}")
            budget = max(5.0, min(40.0, deadline.remaining() - 8.0))
            converted = office_to_pdf(work / f"source.{file_type}", d, timeout=budget)
            pdf_path = d / "render.pdf"
            converted.replace(pdf_path)
        else:
            return PreviewInfo(error=f"No preview for .{file_type} files.")

        with _pdfium_lock:
            pdf = pdfium.PdfDocument(str(pdf_path))
            try:
                pages = len(pdf)
            finally:
                pdf.close()
        if pages == 0:
            return PreviewInfo(error="The document has no pages to preview.")
        return PreviewInfo(available=True, pages=pages, pdf_path=pdf_path)
    except PreviewError as exc:
        return PreviewInfo(error=str(exc))
    except Exception as exc:  # noqa: BLE001 - a preview must never break the parse
        logger.exception("Preview preparation failed")
        return PreviewInfo(error=f"Preview failed: {exc}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_pdf_page(pdf_path: Path, page: int, dpi: int = DEFAULT_DPI) -> bytes:
    """Render a 1-based page of a PDF to PNG bytes."""
    with _pdfium_lock:
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
        except pdfium.PdfiumError as exc:
            raise PreviewError(f"The PDF could not be opened for preview: {exc}") from exc
        try:
            if page < 1 or page > len(pdf):
                raise PreviewError(f"Page {page} does not exist (document has {len(pdf)} pages).")
            image = pdf[page - 1].render(scale=dpi / 72, may_draw_forms=False).to_pil().convert("RGB")
        except pdfium.PdfiumError as exc:
            raise PreviewError(f"Page {page} could not be rendered: {exc}") from exc
        finally:
            pdf.close()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def render_page_pil(pdf_path: Path, page: int, dpi: int = 200):
    """Render a 1-based PDF page to a PIL image (used by the region router)."""
    with _pdfium_lock:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            return pdf[page - 1].render(scale=dpi / 72, may_draw_forms=False).to_pil().convert("RGB")
        finally:
            pdf.close()


def convert_media_to_png(blob: bytes, ext: str, timeout: float = 25.0) -> bytes | None:
    """Convert EMF/WMF/other vector pictures to PNG with headless LibreOffice. None on failure."""
    soffice = find_soffice()
    if soffice is None or not blob:
        return None
    with tempfile.TemporaryDirectory(prefix="media-") as tmp:
        src = Path(tmp) / f"media.{ext or 'emf'}"
        src.write_bytes(blob)
        out = Path(tmp) / "out"
        out.mkdir()
        with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
            try:
                subprocess.run(
                    [soffice, f"-env:UserInstallation=file://{profile}", "--headless", "--norestore",
                     "--convert-to", "png", "--outdir", str(out), str(src)],
                    capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                return None
        png = out / "media.png"
        return png.read_bytes() if png.exists() else None


def render_image(src: Path) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)
        if max(img.size) > MAX_IMAGE_SIDE:
            img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, "white")
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def page_png(document_id: str, page: int, dpi: int = DEFAULT_DPI) -> bytes:
    d = _doc_dir(document_id)
    if not d.exists():
        raise PreviewError("Preview expired or unknown document. Parse the file again.")
    cached = d / f"page-{page}-{dpi}.png"
    if cached.exists():
        return cached.read_bytes()

    images = sorted(d.glob("source.*"))
    pdf_path = d / "render.pdf"
    if pdf_path.exists():
        data = render_pdf_page(pdf_path, page, dpi)
    elif images and images[0].suffix.lstrip(".") in IMAGE_TYPES:
        if page != 1:
            raise PreviewError("Images have a single page.")
        data = render_image(images[0])
    else:
        raise PreviewError("No preview available for this document.")
    cached.write_bytes(data)
    return data


def preview_from_upload(src: Path, file_type: str, page: int, dpi: int) -> bytes:
    """Stateless one-shot preview for POST /api/preview (page is 1-based)."""
    if file_type in IMAGE_TYPES:
        if page != 1:
            raise PreviewError("Images have a single page.")
        return render_image(src)
    with tempfile.TemporaryDirectory(prefix="preview-") as tmp:
        work = Path(tmp)
        if file_type == "pdf":
            return render_pdf_page(src, page, dpi)
        if file_type in OFFICE_TYPES:
            staged = work / f"source.{file_type}"
            shutil.copyfile(src, staged)
            pdf = office_to_pdf(staged, work)
            return render_pdf_page(pdf, page, dpi)
    raise PreviewError(f"No preview for .{file_type} files.")


# ---------------------------------------------------------------------------
# Locating blocks inside the rendered PDF (DOCX / XLSX)
# ---------------------------------------------------------------------------


def _first_line(text: str, n: int = 60) -> str:
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            return line[:n]
    return ""


def _find(pdf: pdfium.PdfDocument, needle: str, page_index: int = 0, char_index: int = 0):
    """Find `needle` from (page, char) onwards. Returns (page_index, start, end) or None."""
    needle = needle.strip()
    if len(needle) < 2:
        return None
    for pi in range(page_index, len(pdf)):
        tp = pdf[pi].get_textpage()
        try:
            hit = tp.search(needle, index=char_index if pi == page_index else 0, match_case=False).get_next()
            if hit:
                return pi, hit[0], hit[0] + hit[1]
        finally:
            tp.close()
    return None


def _range_box(pdf: pdfium.PdfDocument, pi: int, start: int, end: int) -> BBox | None:
    """Normalized top-left box around the characters [start, end) of one page."""
    page = pdf[pi]
    w, h = page.get_size()
    tp = page.get_textpage()
    try:
        end = min(end, tp.count_chars())
        xs0, ys0, xs1, ys1 = [], [], [], []
        for i in range(start, end):
            l, b, r, t = tp.get_charbox(i)
            if r - l <= 0 or t - b <= 0:  # newlines / zero-size glyphs
                continue
            xs0.append(l), ys0.append(b), xs1.append(r), ys1.append(t)
    finally:
        tp.close()
    if not xs0:
        return None
    return (
        round(min(xs0) / w, 6),
        round(1 - max(ys1) / h, 6),
        round(max(xs1) / w, 6),
        round(1 - min(ys0) / h, 6),
    )


def _union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def annotate_blocks(info: PreviewInfo, blocks: list[DocumentBlock], file_type: str) -> None:
    """Attach metadata['preview'] = {page, bbox} so the viewer can draw each block.

    PDF/images: the block's own page/bbox are already in preview space (nothing to do).
    PPTX: slide N is PDF page N and shape geometry is exact.
    DOCX/XLSX: no native coordinates, so blocks are located by searching their text in
    the converted PDF. Blocks that cannot be located get a page but no bbox.
    """
    if not info.available or info.pdf_path is None or file_type not in OFFICE_TYPES:
        return

    if file_type == "pptx":
        for b in blocks:
            b.metadata["preview"] = {"page": min(b.page, info.pages), "bbox": list(b.bbox) if b.bbox else None}
        return

    with _pdfium_lock:
        pdf = pdfium.PdfDocument(str(info.pdf_path))
        try:
            page_cursor, char_cursor = 0, 0  # blocks are in document order: search forward
            for b in blocks:
                deadline.check()
                if b.type in (BlockType.FIGURE, BlockType.CHART):
                    b.metadata["preview"] = {"page": min(page_cursor + 1, info.pages), "bbox": None}
                    continue

                rows = b.metadata.get("rows")
                if b.type == BlockType.TABLE and rows:
                    cells = [" ".join(str(c).split()) for r in rows for c in r if c not in (None, "")]
                    first = cells[0] if cells else _first_line(b.content)
                    last = cells[-1] if cells else ""
                else:
                    lines = [" ".join(ln.split()) for ln in b.content.splitlines() if ln.strip()]
                    first = lines[0][:60] if lines else ""
                    last = lines[-1][-40:] if lines else ""

                start = _find(pdf, first, page_cursor, char_cursor)
                if not start:
                    b.metadata["preview"] = {"page": min(page_cursor + 1, info.pages), "bbox": None}
                    continue
                pi, s0, e0 = start
                end = _find(pdf, last, pi, s0) if last else None
                e1 = end[2] if end and end[0] == pi else None
                if e1 is None:  # block runs onto another page: take the rest of this one
                    e1 = 10**9 if end else e0
                box = _range_box(pdf, pi, s0, max(e1, e0))
                page_cursor, char_cursor = pi, s0
                b.metadata["preview"] = {"page": pi + 1, "bbox": list(box) if box else None}
        finally:
            pdf.close()
