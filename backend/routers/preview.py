"""Document preview endpoint for generating high-quality page previews."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import pymupdf

router = APIRouter(prefix="/api", tags=["preview"])


def generate_pdf_preview_from_bytes(
    file_bytes: bytes,
    page: int = 0,
    dpi: int = 200,
    format: Literal["png", "jpeg"] = "png",
) -> bytes:
    """Generate a high-quality preview image from PDF bytes.
    
    Args:
        file_bytes: PDF file content as bytes
        page: Page number (0-indexed)
        dpi: Resolution for rendering (higher = better quality)
        format: Image format (png or jpeg)
    
    Returns:
        Image bytes
    """
    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    
    if page >= doc.page_count:
        raise HTTPException(status_code=400, detail=f"Page {page} does not exist")
    
    pdf_page = doc[page]
    
    # Calculate zoom based on DPI (72 DPI is default, so zoom = dpi/72)
    zoom = dpi / 72
    mat = pymupdf.Matrix(zoom, zoom)
    
    # Render page to pixmap
    pix = pdf_page.get_pixmap(matrix=mat, alpha=False)
    
    # Convert to bytes
    if format == "png":
        img_bytes = pix.tobytes(output="png")
    else:  # jpeg
        img_bytes = pix.tobytes(output="jpeg")
    
    doc.close()
    return img_bytes


def generate_image_preview_from_bytes(
    file_bytes: bytes,
    max_size: int = 2000,
) -> bytes:
    """Generate a preview from image bytes.
    
    Args:
        file_bytes: Image file content as bytes
        max_size: Maximum dimension (width or height) in pixels
    
    Returns:
        Image bytes (PNG format)
    """
    from PIL import Image
    
    img = Image.open(io.BytesIO(file_bytes))
    
    # Calculate new size maintaining aspect ratio
    width, height = img.size
    if max(width, height) > max_size:
        if width > height:
            new_width = max_size
            new_height = int(height * (max_size / width))
        else:
            new_height = max_size
            new_width = int(width * (max_size / height))
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # Convert to RGB if necessary
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    # Save to bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", quality=95)
    return buffer.getvalue()


@router.post("/preview")
async def get_document_preview(
    file: UploadFile,
    page: int = Query(0, ge=0, description="Page number for PDFs (0-indexed)"),
    dpi: int = Query(200, ge=72, le=300, description="DPI for PDF rendering (higher = better quality)"),
):
    """Generate a high-quality preview image from uploaded file.
    
    For PDFs: Renders the specified page at the given DPI
    For images: Returns a resized version maintaining aspect ratio
    For other formats: Returns an error
    
    Query parameters:
        page: Page number for PDFs (default: 0)
        dpi: DPI for PDF rendering (default: 200, range: 72-300)
    
    Returns: Image data (PNG format)
    """
    file_bytes = await file.read()
    file_ext = file.filename.split(".")[-1].lower() if file.filename else ""
    
    try:
        if file_ext == "pdf":
            img_bytes = await run_in_threadpool(
                generate_pdf_preview_from_bytes, file_bytes, page, dpi, "png"
            )
            media_type = "image/png"
        elif file_ext in ("jpg", "jpeg", "png", "gif"):
            img_bytes = await run_in_threadpool(
                generate_image_preview_from_bytes, file_bytes, max_size=2000
            )
            media_type = "image/png"
        else:
            raise HTTPException(
                status_code=400,
                detail="Preview not available for this file type"
            )
        
        return Response(content=img_bytes, media_type=media_type)
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate preview: {str(e)}")
