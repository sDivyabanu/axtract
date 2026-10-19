"""Page-level vision-language OCR engine (LightOnOCR-3 served by llama.cpp's `llama-server`).

Off by default. Enable with AXTRACT_VLM_OCR:
  off      never used (default; AXTRACT behaves exactly as before)
  scanned  only pages the router classifies as scanned or image-dominated
  all      every PDF page

The model runs in a separate local process (no external service): start `llama-server` with the GGUF weights and point
AXTRACT_VLM_URL at it (see docs/VLM_OCR.md). Each page is rendered, sent once with the model's "grounding" prompt, and the
reply's layout blocks become DocumentBlocks:
  * bbox: the box the model predicted for the block on the page image (metadata bbox_source="model_predicted"). It is the
    model's estimate, not a coordinate read from the PDF, and is marked as such.
  * content: the model's Markdown for the block, emitted verbatim (LaTeX math, HTML tables).
  * page headers, footers and page numbers become HEADER/FOOTER blocks, so they stay in the JSON and out of the body Markdown.
If the server is unreachable, times out or returns nothing usable, the caller falls back to the existing extractors for that
page and records a VLM_* error, so a missing model never fails a document.
"""
from __future__ import annotations

import base64
import io
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
import pypdfium2 as pdfium

from models.document import BlockType, DocumentBlock
from services.vlm_ocr import GroundedBlock, html_table_to_grid, is_margin, parse_grounded

logger = logging.getLogger(__name__)

ENGINE = "lightonocr"

_LABEL_TYPES = {
    "title": BlockType.HEADING, "section_header": BlockType.HEADING, "section-header": BlockType.HEADING,
    "table": BlockType.TABLE, "image": BlockType.FIGURE, "figure": BlockType.FIGURE, "chart": BlockType.CHART,
    "formula": BlockType.EQUATION, "equation": BlockType.EQUATION, "list": BlockType.LIST, "list_item": BlockType.LIST,
}
_HEADING = re.compile(r"^(#{1,6})\s+")
_unavailable_until = 0.0


class VLMError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def mode() -> str:
    m = os.environ.get("AXTRACT_VLM_OCR", "off").strip().lower()
    return m if m in ("off", "scanned", "all") else "off"


def scan_like(route: str, analysis: dict | None = None) -> bool:
    """A page that is a picture of text: classified scanned, or covered by images even if it carries an OCR text layer."""
    return route in ("scanned", "mixed") or bool(analysis and (analysis.get("image_coverage") or 0) >= 0.85)


def enabled_for(route: str, analysis: dict | None = None) -> bool:
    m = mode()
    return m == "all" or (m == "scanned" and scan_like(route, analysis))


def profile_for(route: str, analysis: dict | None = None) -> str:
    """'scan' when a separate scan model is configured (AXTRACT_VLM_SCAN_MODEL_ID) and the page is scan-like."""
    return "scan" if os.environ.get("AXTRACT_VLM_SCAN_MODEL_ID") and scan_like(route, analysis) else "general"


def settings(profile: str = "general") -> dict:
    env = os.environ.get
    cfg = {"profile": "general", "url": env("AXTRACT_VLM_URL", "http://127.0.0.1:8090").rstrip("/"),
           "model": env("AXTRACT_VLM_MODEL_ID", "LightOnOCR-3-0.8B-Q8_0"),
           "longest": int(env("AXTRACT_VLM_LONGEST", "2048")),
           "max_tokens": int(env("AXTRACT_VLM_MAX_TOKENS", "4096")),
           "timeout": float(env("AXTRACT_VLM_PAGE_SECONDS", "600"))}
    if profile == "scan" and env("AXTRACT_VLM_SCAN_MODEL_ID"):
        # A larger model for pictures of text (handwriting, old prints): slower, used only where it pays off.
        cfg.update(profile="scan", model=env("AXTRACT_VLM_SCAN_MODEL_ID"),
                   url=env("AXTRACT_VLM_SCAN_URL", cfg["url"]).rstrip("/"),
                   timeout=float(env("AXTRACT_VLM_SCAN_PAGE_SECONDS", "900")))
    return cfg


def render_page_png(pdf_path: Path, page_index: int, longest: int) -> bytes:
    """The page image the model sees: longest side `longest` px (LightOnOCR-3's recommended 2048), rendered with pypdfium2."""
    from services.preview_service import _pdfium_lock

    with _pdfium_lock:  # PDFium is not thread-safe; previews and parses share one lock
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            page = doc[page_index]
            w, h = page.get_size()
            img = page.render(scale=longest / max(w, h, 1.0)).to_pil().convert("RGB")
        finally:
            doc.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def request_body(png: bytes, cfg: dict) -> dict:
    return {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}},
        {"type": "text", "text": "grounding"}]}],
        "temperature": 0.0, "top_p": 1.0, "max_tokens": cfg["max_tokens"], "seed": 0, "repeat_penalty": 1.0}


def _cache_path(png: bytes, body: dict, cfg: dict) -> Path | None:
    """Opt-in cache of raw model replies (AXTRACT_VLM_CACHE_DIR). Keyed by the exact page image and every request setting,
    so a changed model, resolution, prompt or sampling setting never reuses an old reply. Off by default because the cache
    holds document text unencrypted."""
    root = os.environ.get("AXTRACT_VLM_CACHE_DIR")
    if not root:
        return None
    settings_only = {k: v for k, v in body.items() if k != "messages"}
    key = hashlib.sha256(png + json.dumps([cfg["model"], cfg["longest"], settings_only, body["messages"][0]["content"][1]],
                                          sort_keys=True).encode()).hexdigest()
    return Path(root) / key[:2] / f"{key}.json"


def transcribe(png: bytes, cfg: dict) -> dict:
    """One grounding request; returns {"text", "finish", "usage", "seconds", "cached"}."""
    global _unavailable_until
    if time.monotonic() < _unavailable_until:
        raise VLMError("VLM_UNAVAILABLE", "the VLM OCR server was unreachable moments ago; using the standard extractors")
    body = request_body(png, cfg)
    cache = _cache_path(png, body, cfg)
    if cache is not None and cache.is_file():
        try:
            return {**json.loads(cache.read_text(encoding="utf8")), "cached": True}
        except (OSError, ValueError):
            pass  # unreadable cache entry: ask the model again
    t0 = time.perf_counter()
    try:
        r = httpx.post(cfg["url"] + "/v1/chat/completions", json=body, timeout=cfg["timeout"])
        r.raise_for_status()
        data = r.json()
    except httpx.ConnectError as exc:
        _unavailable_until = time.monotonic() + 30  # do not wait on a dead server for every page
        raise VLMError("VLM_UNAVAILABLE", f"VLM OCR server not reachable at {cfg['url']}: {exc}") from exc
    except httpx.TimeoutException as exc:
        raise VLMError("VLM_TIMEOUT", f"VLM OCR did not answer within {cfg['timeout']:.0f} s") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise VLMError("VLM_FAILED", f"VLM OCR request failed: {exc}") from exc
    choice = (data.get("choices") or [{}])[0]
    reply = {"text": (choice.get("message") or {}).get("content") or "", "finish": choice.get("finish_reason"),
             "usage": data.get("usage"), "seconds": round(time.perf_counter() - t0, 2)}
    if cache is not None and reply["text"]:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(reply), encoding="utf8")
            os.replace(tmp, cache)  # atomic: a partial write is never read back as a finished reply
        except OSError:
            logger.warning("could not write the VLM reply cache at %s", cache)
    return {**reply, "cached": False}


def _trim_repetition(text: str, max_repeats: int = 3) -> tuple[str, bool]:
    """Cut a degenerate decoding loop: the same non-empty line repeated back to back more than max_repeats times."""
    out, run, prev, trimmed = [], 0, None, False
    for line in text.split("\n"):
        key = line.strip()
        run = run + 1 if key and key == prev else 1
        prev = key
        if run > max_repeats:
            trimmed = True
            continue
        out.append(line)
    return "\n".join(out), trimmed


_MARGIN_IMAGES = {"header_image", "footer_image"}
_PICTURES = {"image", "figure", "header_image", "footer_image"}


def _to_block(g: GroundedBlock, page_number: int, index: int, cfg: dict, review: bool) -> DocumentBlock:
    content, meta = g.content, {}
    if is_margin(g) or g.label in _MARGIN_IMAGES:  # running heads, folios, logos in the page margin
        btype = BlockType.HEADER if (g.bbox[1] + g.bbox[3]) / 2 < 0.5 and "footer" not in g.label else BlockType.FOOTER
    else:
        btype = _LABEL_TYPES.get(g.label, BlockType.PARAGRAPH)
        m = _HEADING.match(content)
        if m and "\n" not in content.strip():
            btype, meta["heading_level"] = BlockType.HEADING, len(m.group(1))
            content = content[m.end():]
    verbatim = True
    if btype == BlockType.CHART:  # the model reads chart values off the picture: an estimate, never source data
        review = True
        meta["values_estimated_from_image"] = True
        meta["flags"] = ["chart_values_estimated"]
    low = content.strip().lower()
    if btype != BlockType.CHART and low.startswith("<table") and low.endswith("</table>"):
        grid = html_table_to_grid(content)
        if grid is not None:  # canonical table: rows + spans, rendered by the standard Markdown writer
            rows, merged, header_rows = grid
            btype, verbatim = BlockType.TABLE, False
            meta.update(rows=rows, merged_cells={"source": "extractor", "merged_regions": merged}, header_rows=header_rows)
            content = "\n".join(" | ".join(r) for r in rows)
        else:
            review = True
            meta["flags"] = ["table_markup_unparsed"]
    if g.label in _PICTURES and btype != BlockType.TABLE:
        # The model describes pictures in its own words. That description is not text of the document, so it is kept as
        # labelled metadata and never emitted as body text.
        meta.update(description=content, description_source="model_generated")
        content, verbatim = "[image]", False
    meta.update({"route": "vlm", "engine": ENGINE, "model": cfg["model"], "vlm_profile": cfg["profile"],
                 "vlm_label": g.label, "vlm_index": index,
                 "bbox_source": "model_predicted", "markdown_verbatim": verbatim})
    return DocumentBlock(id=f"p{page_number}-vlm{index}", type=btype, content=content, page=page_number, bbox=g.bbox,
                         confidence=None, extractor=ENGINE, requires_review=review, metadata=meta)


def page_blocks(pdf_path: Path, page_number: int, profile: str = "general") -> tuple[list[DocumentBlock], dict]:
    """Transcribe one page (1-based). Raises VLMError when no usable output was produced."""
    cfg = settings(profile)
    try:
        png = render_page_png(pdf_path, page_number - 1, cfg["longest"])
    except Exception as exc:  # noqa: BLE001 - an unrenderable page falls back to the standard extractors
        raise VLMError("VLM_RENDER_FAILED", f"page could not be rendered for VLM OCR: {exc}") from exc
    reply = transcribe(png, cfg)
    text, looped = _trim_repetition(reply["text"])
    truncated = reply["finish"] == "length"
    grounded = parse_grounded(text)
    if grounded is None:  # the model answered without layout placeholders: keep the text as one unlocated block
        grounded = [GroundedBlock("text", (0.0, 0.0, 1.0, 1.0), text.strip())] if text.strip() else []
    grounded = [g for g in grounded if g.content or g.label in ("image", "figure")]
    if not any(g.content for g in grounded):
        raise VLMError("VLM_EMPTY", "VLM OCR returned no text for this page")
    review = looped or truncated
    blocks = [_to_block(g, page_number, i, cfg, review) for i, g in enumerate(grounded)]
    info = {"seconds": reply["seconds"], "finish": reply["finish"], "usage": reply["usage"], "repetition_trimmed": looped,
            "cached": reply["cached"], "profile": cfg["profile"], "model": cfg["model"]}
    return blocks, info
