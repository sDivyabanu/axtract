"""Extractors for container formats: TIFF/HEIC images, email (.eml/.msg with
attachments parsed recursively) and legacy Office files via LibreOffice.
"""

from __future__ import annotations

import shutil
import tempfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import ClassVar

from extractors.base import BaseExtractor, ExtractionResult
from models.document import BlockType, DocumentBlock
from models.errors import AppError, DocumentError
from utils.files import get_extension


def _merge_results(results: list[ExtractionResult]) -> ExtractionResult:
    """Concatenate results, offsetting each one's pages after the first."""
    blocks, errors, assets = [], [], {}
    page_cursor = 0
    for res in results:
        for b in res.blocks:
            b.page += page_cursor
            blocks.append(b)
        for e in res.errors:
            e.page = (e.page or 1) + page_cursor
            errors.append(e)
        for key, val in res.assets.items():
            assets[f"{page_cursor}:{key}"] = val
        page_cursor += max(res.page_count, 1)
    return ExtractionResult(page_count=max(page_cursor, 1), blocks=blocks, errors=errors, assets=assets)


class TiffHeicExtractor(BaseExtractor):
    """TIFF (multi-frame) and HEIC images: decode frames, then OCR each."""

    name: ClassVar[str] = "tiff-heic"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"tif", "tiff", "heic"})

    def extract(self, file_path: Path) -> ExtractionResult:
        from PIL import Image, ImageOps, ImageSequence

        if file_path.suffix.lower() == ".heic":
            try:
                import pillow_heif  # type: ignore[import-not-found]
                pillow_heif.register_heif_opener()
            except ImportError as exc:
                raise AppError(
                    "UNSUPPORTED_FORMAT",
                    "HEIC support requires the pillow-heif package.",
                    status_code=415,
                ) from exc

        try:
            img = Image.open(file_path)
            frames = [ImageOps.exif_transpose(f.convert("RGB")) for f in ImageSequence.Iterator(img)]
        except Exception as exc:
            raise AppError("INVALID_FILE", f"Could not open image: {exc}", status_code=422) from exc

        from extractors.ocr_extractor import OCRExtractor
        ocr = OCRExtractor()
        results: list[ExtractionResult] = []
        with tempfile.TemporaryDirectory() as td:
            for i, frame in enumerate(frames):
                frame_path = Path(td) / f"frame-{i}.png"
                frame.save(frame_path)
                res = ocr.extract(frame_path)
                results.append(res)
        return _merge_results(results)


class EmailExtractor(BaseExtractor):
    """.eml emails: headers, body and attachments parsed recursively."""

    name: ClassVar[str] = "email"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"eml"})

    def extract(self, file_path: Path) -> ExtractionResult:
        msg = BytesParser(policy=policy.default).parsebytes(file_path.read_bytes())
        blocks, errors = [], []
        idx = 0

        def add(btype: BlockType, text: str, meta: dict | None = None) -> None:
            nonlocal idx
            if not text.strip():
                return
            blocks.append(DocumentBlock(
                id=f"eml-{idx}", type=btype, content=text.strip(), page=1,
                bbox=None, confidence=None, extractor=self.name, reading_order=None,
                requires_review=False, metadata=meta or {},
            ))
            idx += 1

        subject = str(msg.get("Subject", "") or "")
        add(BlockType.HEADING, subject or "(no subject)", {"heading_level": 1})
        header_lines = "\n".join(
            f"{k}: {msg.get(k)}" for k in ("From", "To", "Date", "Cc") if msg.get(k)
        )
        add(BlockType.PARAGRAPH, header_lines)

        body = ""
        body_part = msg.get_body(preferencelist=("plain",))
        html_part = msg.get_body(preferencelist=("html",))
        with tempfile.TemporaryDirectory() as td:
            if body_part is not None:
                body = body_part.get_content()
            elif html_part is not None:
                html_file = Path(td) / "body.html"
                html_file.write_text(html_part.get_content(), encoding="utf-8")
                from extractors.text_extractors import HtmlExtractor
                res = HtmlExtractor().extract(html_file)
                body = "\n\n".join(b.content for b in res.blocks)
            for para in [p.strip() for p in body.split("\n\n") if p.strip()]:
                add(BlockType.PARAGRAPH, para)

            attachments = _collect_attachments(msg)
            if attachments:
                add(BlockType.HEADING, "Attachments", {"heading_level": 2})
            results: list[ExtractionResult] = []
            for filename, data in attachments:
                parsed = _parse_attachment(filename, data, Path(td))
                if parsed is None:
                    errors.append(DocumentError(
                        message=f"Attachment skipped (unsupported or unreadable): {filename}",
                        page=1,
                    ))
                    continue
                add(BlockType.PARAGRAPH, f"Attachment: {filename}")
                results.append(parsed)

            if results:
                merged = _merge_results(results)
                for b in merged.blocks:
                    b.page += 1  # body is page 1
                for e in merged.errors:
                    e.page = (e.page or 1) + 1
                total = merged.page_count + 1
                return ExtractionResult(page_count=total, blocks=blocks + merged.blocks, errors=errors + merged.errors)

        return ExtractionResult(page_count=1, blocks=blocks, errors=errors)


def _collect_attachments(msg) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if not filename:
            continue
        try:
            data = part.get_payload(decode=True) or b""
        except Exception:  # noqa: BLE001
            continue
        if data:
            out.append((filename, data))
    return out


def _parse_attachment(filename: str, data: bytes, tmp_dir: Path) -> ExtractionResult | None:
    """Parse one attachment through the normal registry, recursively."""
    from extractors.registry import get_extractor

    ext = get_extension(filename)
    extractor = get_extractor(ext) if ext else None
    if extractor is None:
        return None
    path = tmp_dir / Path(filename).name
    path.write_bytes(data)
    try:
        return extractor.extract(path)
    except Exception:  # noqa: BLE001
        return None


class MsgExtractor(BaseExtractor):
    """Outlook .msg files via extract-msg."""

    name: ClassVar[str] = "extract-msg"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"msg"})

    def extract(self, file_path: Path) -> ExtractionResult:
        try:
            import extract_msg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise AppError(
                "UNSUPPORTED_FORMAT",
                "MSG support requires the extract-msg package.",
                status_code=415,
            ) from exc

        msg = extract_msg.Message(str(file_path))
        eml_bytes = msg.to_bytes() if hasattr(msg, "to_bytes") else None
        if eml_bytes:
            with tempfile.TemporaryDirectory() as td:
                eml_path = Path(td) / "converted.eml"
                eml_path.write_bytes(eml_bytes)
                return EmailExtractor().extract(eml_path)
        raise AppError("UNSUPPORTED_FORMAT", "Could not convert MSG file.", status_code=422)


class LegacyOfficeExtractor(BaseExtractor):
    """DOC/PPT/XLS via headless LibreOffice conversion to modern formats.

    Raises a structured error when LibreOffice is not installed, so the API
    returns a clean message instead of crashing.
    """

    name: ClassVar[str] = "libreoffice"
    supported_extensions: ClassVar[frozenset[str]] = frozenset({"doc", "ppt", "xls"})

    _TARGET = {"doc": "docx", "ppt": "pptx", "xls": "xlsx"}

    def _soffice(self) -> str:
        found = shutil.which("soffice") or shutil.which("libreoffice")
        if not found:
            mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
            if mac.exists():
                return str(mac)
        if not found:
            raise AppError(
                "UNSUPPORTED_FORMAT",
                "Legacy Office formats require LibreOffice (soffice) on the server.",
                status_code=415,
            )
        return found

    def extract(self, file_path: Path) -> ExtractionResult:
        from extractors.registry import get_extractor

        ext = file_path.suffix.lower().lstrip(".")
        target = self._TARGET[ext]
        soffice = self._soffice()
        with tempfile.TemporaryDirectory() as td:
            import subprocess
            proc = subprocess.run(
                [soffice, "--headless", "--convert-to", target, "--outdir", td, str(file_path)],
                capture_output=True, timeout=120,
            )
            converted = Path(td) / f"{file_path.stem}.{target}"
            if proc.returncode != 0 or not converted.exists():
                raise AppError(
                    "PROCESSING_FAILED",
                    f"LibreOffice could not convert {file_path.name}.",
                    status_code=422,
                )
            delegate = get_extractor(target)
            assert delegate is not None
            return delegate.extract(converted)
