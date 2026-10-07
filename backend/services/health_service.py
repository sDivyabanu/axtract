"""Document health and hidden content detector.

Implements:
1. Invisible/malicious content scan (white-on-white text, microscopic text, hidden layers)
2. Prompt injection detection
3. Document health report generation
"""

from __future__ import annotations

import logging
import re
from typing import Any

from models.document import BlockType, DocumentBlock

logger = logging.getLogger(__name__)


def detect_invisible_text(content: str) -> dict[str, Any]:
    """Detect potentially invisible or malicious text.
    
    Checks for:
    - White text on white background indicators
    - Microscopic font size mentions
    - Zero-width characters
    - Hidden Unicode characters
    
    Returns detection results.
    """
    issues = []
    
    # Check for white-on-white text indicators
    white_text_patterns = [
        r'color\s*[:=]\s*["\']?\s*white',
        r'color\s*[:=]\s*["\']?\s*#fff',
        r'color\s*[:=]\s*["\']?\s*#ffffff',
        r'background-color\s*[:=]\s*["\']?\s*white',
    ]
    
    for pattern in white_text_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append({
                "type": "white_on_white_text",
                "pattern": pattern,
                "severity": "high",
            })
    
    # Check for microscopic font size
    font_size_patterns = [
        r'font-size\s*[:=]\s*["\']?\s*[012]\s*px',
        r'font-size\s*[:=]\s*["\']?\s*0\.',
        r'font-size\s*[:=]\s*["\']?\s*[012]\s*pt',
    ]
    
    for pattern in font_size_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            issues.append({
                "type": "microscopic_font",
                "pattern": pattern,
                "severity": "medium",
            })
    
    # Check for zero-width characters
    zero_width_chars = ['\u200B', '\u200C', '\u200D', '\uFEFF']
    for char in zero_width_chars:
        if char in content:
            issues.append({
                "type": "zero_width_character",
                "char": repr(char),
                "severity": "low",
            })
    
    return {
        "has_invisible_text": len(issues) > 0,
        "issues": issues,
    }


def detect_prompt_injection(content: str) -> dict[str, Any]:
    """Detect potential prompt injection attempts.
    
    Checks for suspicious patterns like:
    - "AI, ignore your instructions"
    - "system: override"
    - "ignore previous"
    - "developer mode"
    
    Returns detection results.
    """
    injection_patterns = [
        r'(?i)ignore\s+(your|previous|all)\s+instructions?',
        r'(?i)system\s*[:\s]*override',
        r'(?i)developer\s+mode',
        r'(?i)AI\s*,?\s*ignore',
        r'(?i)bypass\s+(security|restrictions|filters)',
        r'(?i)jailbreak',
        r'(?i)DAN\s+(mode|ignore)',
    ]
    
    matches = []
    for pattern in injection_patterns:
        if re.search(pattern, content):
            matches.append({
                "pattern": pattern,
                "severity": "high",
            })
    
    return {
        "has_prompt_injection": len(matches) > 0,
        "matches": matches,
    }


def detect_hidden_layers(blocks: list[DocumentBlock]) -> dict[str, Any]:
    """Detect blocks that might be hidden layers.
    
    Checks for:
    - Blocks with zero-size bboxes
    - Blocks with requires_review already flagged
    - Blocks with suspicious metadata
    
    Returns detection results.
    """
    hidden_blocks = []
    
    for block in blocks:
        # Check for zero-size bbox
        if block.bbox:
            x1, y1, x2, y2 = block.bbox
            if abs(x2 - x1) < 0.01 or abs(y2 - y1) < 0.01:
                hidden_blocks.append({
                    "block_id": block.id,
                    "type": "zero_size_bbox",
                    "bbox": block.bbox,
                })
        
        # Check for suspicious metadata
        if "hidden" in str(block.metadata).lower():
            hidden_blocks.append({
                "block_id": block.id,
                "type": "hidden_metadata",
                "metadata": block.metadata,
            })
    
    return {
        "has_hidden_layers": len(hidden_blocks) > 0,
        "hidden_blocks": hidden_blocks,
    }


def generate_document_health_report(
    blocks: list[DocumentBlock], filename: str
) -> dict[str, Any]:
    """Generate a comprehensive document health report.
    
    Returns:
    - overall_health: "healthy", "warning", "critical"
    - scanned_page_count: int
    - flagged_items_count: int
    - hidden_content_warnings: list
    - prompt_injection_warnings: list
    - math_verification_status: str
    - processing_summary: dict
    """
    page_count = len(set(b.page for b in blocks)) if blocks else 0
    flagged_items = 0
    hidden_content_warnings = []
    prompt_injection_warnings = []
    math_verification_status = "unknown"
    
    # Scan all blocks
    for block in blocks:
        # Check for invisible text
        invisible_check = detect_invisible_text(block.content)
        if invisible_check["has_invisible_text"]:
            hidden_content_warnings.extend(invisible_check["issues"])
            flagged_items += len(invisible_check["issues"])
        
        # Check for prompt injection
        injection_check = detect_prompt_injection(block.content)
        if injection_check["has_prompt_injection"]:
            prompt_injection_warnings.extend(injection_check["matches"])
            flagged_items += len(injection_check["matches"])
        
        # Check if flagged for review
        if block.requires_review:
            flagged_items += 1
        
        # Check math verification status
        verification = block.metadata.get("verification", {})
        if verification.get("is_consistent") is False:
            math_verification_status = "failed"
        elif math_verification_status != "failed" and verification.get("is_consistent"):
            math_verification_status = "passed"
    
    # Detect hidden layers
    hidden_layers = detect_hidden_layers(blocks)
    if hidden_layers["has_hidden_layers"]:
        hidden_content_warnings.extend(hidden_layers["hidden_blocks"])
        flagged_items += len(hidden_layers["hidden_blocks"])
    
    # Determine overall health
    if flagged_items == 0:
        overall_health = "healthy"
    elif flagged_items < 5:
        overall_health = "warning"
    else:
        overall_health = "critical"
    
    return {
        "overall_health": overall_health,
        "filename": filename,
        "scanned_page_count": page_count,
        "flagged_items_count": flagged_items,
        "hidden_content_warnings": hidden_content_warnings,
        "prompt_injection_warnings": prompt_injection_warnings,
        "math_verification_status": math_verification_status,
        "block_count": len(blocks),
        "blocks_requiring_review": sum(1 for b in blocks if b.requires_review),
    }


def enhance_blocks_with_health_check(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Enhance blocks with health check flags."""
    enhanced_blocks = []
    
    for block in blocks:
        # Check for invisible text
        invisible_check = detect_invisible_text(block.content)
        
        # Check for prompt injection
        injection_check = detect_prompt_injection(block.content)
        
        # Update metadata
        enhanced_metadata = block.metadata.copy()
        enhanced_metadata.update({
            "health_check": {
                "has_invisible_text": invisible_check["has_invisible_text"],
                "has_prompt_injection": injection_check["has_prompt_injection"],
            },
        })
        
        # Flag for review if issues found
        requires_review = (
            block.requires_review
            or invisible_check["has_invisible_text"]
            or injection_check["has_prompt_injection"]
        )
        
        enhanced_block = DocumentBlock(
            id=block.id,
            type=block.type,
            content=block.content,
            page=block.page,
            bbox=block.bbox,
            confidence=block.confidence,
            extractor=block.extractor,
            reading_order=block.reading_order,
            requires_review=requires_review,
            metadata=enhanced_metadata,
        )
        enhanced_blocks.append(enhanced_block)
    
    return enhanced_blocks
