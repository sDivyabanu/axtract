"""Chart data extraction service using vision models.

Detects charts in documents and extracts structured data including:
- title, chart_type (bar, line, pie, etc.)
- labels, values/data_points
- axes (X and Y labels/scales)
- legend
"""

from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image

from models.document import BlockType, DocumentBlock

logger = logging.getLogger(__name__)


def encode_image_to_base64(image_path: Path) -> str:
    """Encode an image file to base64 string for API transmission."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def crop_chart_region(
    page_image: Image.Image, bbox: tuple[float, float, float, float]
) -> Image.Image:
    """Crop a chart region from a page image using normalized bbox coordinates.
    
    bbox: (x1, y1, x2, y2) normalized to 0.0-1.0
    """
    width, height = page_image.size
    x1, y1, x2, y2 = bbox
    
    # Convert to pixel coordinates
    left = int(x1 * width)
    top = int(y1 * height)
    right = int(x2 * width)
    bottom = int(y2 * height)
    
    return page_image.crop((left, top, right, bottom))


def detect_chart_from_text_nearby(blocks: list[DocumentBlock], chart_block: DocumentBlock) -> str:
    """Detect chart title and type from nearby text blocks."""
    # Look for blocks near the chart on the same page
    nearby_blocks = [
        b for b in blocks
        if b.page == chart_block.page and b.type in (BlockType.HEADING, BlockType.PARAGRAPH)
    ]
    
    # Find closest block before the chart
    chart_order = chart_block.reading_order or 0
    nearby_blocks = [b for b in nearby_blocks if (b.reading_order or 0) < chart_order]
    
    if nearby_blocks:
        # Get the closest block
        closest = max(nearby_blocks, key=lambda b: b.reading_order or 0)
        content = closest.content.lower()
        
        # Detect chart type from text
        chart_type = "unknown"
        if "bar" in content:
            chart_type = "bar"
        elif "line" in content:
            chart_type = "line"
        elif "pie" in content:
            chart_type = "pie"
        elif "scatter" in content:
            chart_type = "scatter"
        elif "area" in content:
            chart_type = "area"
        
        return f"{closest.content} ({chart_type})"
    
    return "Chart"


def extract_chart_data_with_llm(
    image_base64: str, chart_type_hint: str = ""
) -> dict[str, Any]:
    """Extract structured chart data using a multimodal LLM.
    
    This is a placeholder for actual LLM integration. In production,
    you would use services like:
    - GPT-4 Vision
    - Claude 3.5 Sonnet with vision
    - Google Gemini Pro Vision
    - ChartQA specialized model
    
    Returns structured data with:
    - title: str
    - chart_type: str (bar, line, pie, scatter, area, etc.)
    - labels: list[str]
    - values: list[float]
    - axes: dict with x_label, y_label, x_scale, y_scale
    - legend: list[str] or None
    """
    # Placeholder implementation
    # In production, make an API call to a vision model
    
    logger.warning("Chart data extraction with LLM not implemented - using placeholder")
    
    return {
        "title": "Extracted Chart Title",
        "chart_type": chart_type_hint or "bar",
        "labels": ["Category A", "Category B", "Category C"],
        "values": [100, 200, 150],
        "axes": {
            "x_label": "Categories",
            "y_label": "Values",
            "x_scale": "categorical",
            "y_scale": "numeric",
        },
        "legend": ["Series 1"],
        "confidence": 0.5,
        "extraction_method": "placeholder",
    }


def enhance_chart_block(
    block: DocumentBlock,
    page_image: Image.Image | None = None,
    all_blocks: list[DocumentBlock] | None = None,
) -> DocumentBlock:
    """Enhance a chart block with extracted data."""
    if block.type != BlockType.CHART:
        return block
    
    # Detect chart type from nearby text
    chart_info = detect_chart_from_text_nearby(all_blocks or [], block)
    
    # Try to extract structured data if page image is available
    chart_data = {}
    if page_image and block.bbox:
        try:
            # Crop chart region
            chart_image = crop_chart_region(page_image, block.bbox)
            
            # Convert to base64
            buffer = io.BytesIO()
            chart_image.save(buffer, format="PNG")
            image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
            # Extract data with LLM
            chart_data = extract_chart_data_with_llm(image_base64, chart_info)
        except Exception as e:
            logger.error(f"Failed to extract chart data: {e}")
            chart_data = {}
    
    # Update metadata
    enhanced_metadata = block.metadata.copy()
    enhanced_metadata.update({
        "chart_title": chart_info,
        "chart_data": chart_data,
        "data_extracted": bool(chart_data),
    })
    
    return DocumentBlock(
        id=block.id,
        type=block.type,
        content=block.content,
        page=block.page,
        bbox=block.bbox,
        confidence=block.confidence,
        extractor=block.extractor,
        reading_order=block.reading_order,
        requires_review=block.requires_review,
        metadata=enhanced_metadata,
    )


def detect_charts_in_page(page_blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Detect potential chart blocks based on existing figure blocks.
    
    This is a heuristic - charts are often classified as figures initially.
    This function attempts to reclassify them as charts if they meet criteria.
    """
    chart_blocks = []
    
    for block in page_blocks:
        if block.type == BlockType.FIGURE:
            # Heuristic: if image is large and has chart-like dimensions, it might be a chart
            width = block.metadata.get("image_width", 0)
            height = block.metadata.get("image_height", 0)
            
            # Chart aspect ratio is often wider than tall
            if width > 0 and height > 0:
                aspect_ratio = width / height
                if 0.5 < aspect_ratio < 3.0:
                    # Likely a chart
                    chart_block = DocumentBlock(
                        id=block.id,
                        type=BlockType.CHART,
                        content=block.content,
                        page=block.page,
                        bbox=block.bbox,
                        confidence=block.confidence,
                        extractor=block.extractor,
                        reading_order=block.reading_order,
                        requires_review=True,  # Flag for review
                        metadata=block.metadata.copy(),
                    )
                    chart_blocks.append(chart_block)
                else:
                    # Keep as figure
                    chart_blocks.append(block)
            else:
                chart_blocks.append(block)
        else:
            chart_blocks.append(block)
    
    return chart_blocks
