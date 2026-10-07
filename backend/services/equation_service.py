"""Equation to LaTeX conversion service (Python 3.14 compatible).

Since pix2tex is not compatible with Python 3.14, this service uses:
1. Vision LLMs (GPT-4 Vision, Gemini Pro Vision) for LaTeX extraction
2. Future: ONNX-based LaTeX models when available
3. Fallback: OCR-based text extraction
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


def crop_equation_region(
    page_image: Image.Image, bbox: tuple[float, float, float, float]
) -> Image.Image:
    """Crop an equation region from a page image using normalized bbox coordinates.
    
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


def extract_latex_with_vision_llm(image_base64: str) -> dict[str, Any]:
    """Extract LaTeX from equation image using a vision LLM.
    
    This is a placeholder for actual LLM integration. In production,
    you would use services like:
    - GPT-4 Vision with system prompt for LaTeX
    - Claude 3.5 Sonnet with vision
    - Google Gemini Pro Vision
    
    Returns:
    - latex: str (the extracted LaTeX)
    - confidence: float (0-1)
    - method: str ("vision_llm")
    """
    # Placeholder implementation
    logger.warning("LaTeX extraction with vision LLM not implemented - using placeholder")
    
    return {
        "latex": "x^2 + y^2 = r^2",
        "confidence": 0.7,
        "method": "vision_llm_placeholder",
    }


def validate_latex(latex: str) -> bool:
    """Basic validation that the extracted text looks like LaTeX.
    
    Checks for common LaTeX patterns:
    - Superscripts: ^{...} or ^...
    - Subscripts: _{...} or _...
    - Math operators: \frac, \sum, \int, etc.
    - Greek letters: \alpha, \beta, \gamma, etc.
    - Math delimiters: \( \), \[ \], $$, $$
    """
    if not latex:
        return False
    
    # Check for LaTeX patterns
    latex_patterns = [
        r'\\[a-zA-Z]+',  # LaTeX commands
        r'\^[{]',  # Superscripts
        r'_[{]',  # Subscripts
        r'\\frac',  # Fractions
        r'\\sum|\\int|\\prod',  # Operators
        r'\\alpha|\\beta|\\gamma|\\delta|\\theta',  # Greek letters
    ]
    
    pattern_count = sum(1 for pattern in latex_patterns if re.search(pattern, latex))
    return pattern_count >= 1


def enhance_equation_block(
    block: DocumentBlock,
    page_image: Image.Image | None = None,
) -> DocumentBlock:
    """Enhance an equation block with LaTeX extraction."""
    if block.type != BlockType.EQUATION:
        return block
    
    latex_data = {}
    
    # Try to extract LaTeX if page image is available
    if page_image and block.bbox:
        try:
            # Crop equation region
            equation_image = crop_equation_region(page_image, block.bbox)
            
            # Convert to base64
            buffer = io.BytesIO()
            equation_image.save(buffer, format="PNG")
            image_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
            
            # Extract LaTeX with vision LLM
            latex_data = extract_latex_with_vision_llm(image_base64)
            
            # Validate the extracted LaTeX
            if latex_data.get("latex"):
                is_valid = validate_latex(latex_data["latex"])
                latex_data["is_valid"] = is_valid
                
                if not is_valid:
                    logger.warning(f"Extracted LaTeX may be invalid: {latex_data['latex']}")
        except Exception as e:
            logger.error(f"Failed to extract LaTeX from equation: {e}")
            latex_data = {"latex": block.content, "confidence": 0.0, "method": "fallback"}
    
    # Update metadata
    enhanced_metadata = block.metadata.copy()
    enhanced_metadata.update({
        "latex": latex_data.get("latex", block.content),
        "latex_confidence": latex_data.get("confidence", 0.0),
        "latex_method": latex_data.get("method", "none"),
        "latex_validated": latex_data.get("is_valid", False),
    })
    
    # Update content with LaTeX if extracted
    content = latex_data.get("latex", block.content) if latex_data else block.content
    
    return DocumentBlock(
        id=block.id,
        type=block.type,
        content=content,
        page=block.page,
        bbox=block.bbox,
        confidence=block.confidence,
        extractor=block.extractor,
        reading_order=block.reading_order,
        requires_review=block.requires_review or not latex_data.get("is_valid", True),
        metadata=enhanced_metadata,
    )


def detect_equations_in_text(content: str) -> list[tuple[int, int, str]]:
    """Detect potential mathematical equations in text.
    
    Returns list of (start, end, equation) tuples for equations found.
    Uses heuristics to identify math expressions.
    """
    equations = []
    
    # Pattern 1: Expressions with operators
    math_pattern = r'[a-zA-Z_][a-zA-Z0-9_]*\s*[=+\-*/]\s*[a-zA-Z0-9_]+(?:\s*[=+\-*/]\s*[a-zA-Z0-9_]+)*'
    for match in re.finditer(math_pattern, content):
        equations.append((match.start(), match.end(), match.group()))
    
    # Pattern 2: Parenthesized expressions
    paren_pattern = r'\([^)]+[=+\-*/][^)]*\)'
    for match in re.finditer(paren_pattern, content):
        equations.append((match.start(), match.end(), match.group()))
    
    return equations


def render_latex_to_image(latex: str) -> Image.Image | None:
    """Render LaTeX string to an image for verification.
    
    This is a placeholder for actual LaTeX rendering.
    In production, you would use:
    - KaTeX (via node service)
    - MathJax (via node service)
    - matplotlib LaTeX rendering
    
    Returns PIL Image or None if rendering fails.
    """
    # Placeholder implementation
    logger.warning("LaTeX rendering not implemented - returning None")
    return None


def verify_latex_accuracy(
    original_image: Image.Image, rendered_image: Image.Image
) -> float:
    """Verify extracted LaTeX by comparing rendered image with original.
    
    Uses SSIM (Structural Similarity Index) to compare images.
    Returns similarity score 0.0-1.0.
    
    This is a placeholder for actual SSIM calculation.
    """
    # Placeholder implementation
    logger.warning("LaTeX verification not implemented - returning 0.0")
    return 0.0
