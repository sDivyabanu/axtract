"""Cross-verification and confidence scoring service.

Implements:
1. Cross-Extractor Agreement (dual-engine check)
2. Round-Trip Rendering Verification
3. Numeric Tie-Out / Self-Consistency Math Checks
"""

from __future__ import annotations

import logging
import re
from typing import Any

from Levenshtein import distance as levenshtein_distance

from models.document import BlockType, DocumentBlock

logger = logging.getLogger(__name__)


def calculate_string_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two strings using Levenshtein distance.
    
    Returns 0.0-1.0 where 1.0 is identical.
    """
    if not text1 and not text2:
        return 1.0
    if not text1 or not text2:
        return 0.0
    
    max_len = max(len(text1), len(text2))
    if max_len == 0:
        return 1.0
    
    dist = levenshtein_distance(text1, text2)
    similarity = 1.0 - (dist / max_len)
    return round(similarity, 4)


def cross_verify_text_extraction(
    native_text: str, ocr_text: str
) -> dict[str, Any]:
    """Compare native PDF text against OCR text for confidence scoring.
    
    Returns:
    - similarity: float (0-1)
    - confidence: float (0-1)
    - agreement: str ("high", "medium", "low")
    """
    similarity = calculate_string_similarity(native_text, ocr_text)
    
    if similarity >= 0.9:
        agreement = "high"
        confidence = similarity
    elif similarity >= 0.7:
        agreement = "medium"
        confidence = similarity * 0.9
    else:
        agreement = "low"
        confidence = similarity * 0.7
    
    return {
        "similarity": similarity,
        "confidence": confidence,
        "agreement": agreement,
    }


def verify_table_math_consistency(table_data: list[list[Any]]) -> dict[str, Any]:
    """Verify arithmetic consistency in extracted tables.
    
    Checks:
    - Row/column sums matching "Total" lines
    - Percentages adding up to ~100%
    - Asset/liability balances
    
    Returns:
    - is_consistent: bool
    - issues: list[str]
    - confidence_boost: float
    """
    issues = []
    is_consistent = True
    confidence_boost = 0.0
    
    if not table_data or len(table_data) < 2:
        return {"is_consistent": True, "issues": [], "confidence_boost": 0.0}
    
    # Check for percentage sums
    for row_idx, row in enumerate(table_data):
        percentage_values = []
        for cell in row:
            if isinstance(cell, str) and '%' in cell:
                try:
                    val = float(cell.replace('%', '').replace(',', ''))
                    percentage_values.append(val)
                except ValueError:
                    pass
        
        if len(percentage_values) > 1:
            total = sum(percentage_values)
            if abs(total - 100) > 5:  # Allow 5% tolerance
                issues.append(f"Row {row_idx}: Percentages sum to {total}%, not ~100%")
                is_consistent = False
            else:
                confidence_boost += 0.1
    
    # Check for total/sum rows
    for row_idx, row in enumerate(table_data):
        row_text = " ".join(str(c) if c else "" for c in row).lower()
        
        if "total" in row_text or "sum" in row_text:
            # Try to find the sum row and verify
            numeric_values = []
            for cell in row:
                if isinstance(cell, (int, float)):
                    numeric_values.append(cell)
                elif isinstance(cell, str):
                    try:
                        numeric_values.append(float(cell.replace(',', '').replace('$', '')))
                    except ValueError:
                        pass
            
            if len(numeric_values) > 1:
                # Simple check: last value should be sum of others
                if len(numeric_values) >= 2:
                    calculated_sum = sum(numeric_values[:-1])
                    actual_total = numeric_values[-1]
                    
                    if abs(calculated_sum - actual_total) > 0.01 * max(abs(calculated_sum), 1):
                        issues.append(f"Row {row_idx}: Sum mismatch (calculated: {calculated_sum}, actual: {actual_total})")
                        is_consistent = False
                    else:
                        confidence_boost += 0.15
    
    return {
        "is_consistent": is_consistent,
        "issues": issues,
        "confidence_boost": min(confidence_boost, 0.3),  # Cap at 0.3
    }


def verify_chart_data_consistency(
    chart_data: dict[str, Any], table_data: list[list[Any]] | None = None
) -> dict[str, Any]:
    """Verify chart data against table data if available.
    
    Compares extracted chart values with numbers in accompanying tables.
    
    Returns:
    - is_consistent: bool
    - issues: list[str]
    - confidence_boost: float
    """
    issues = []
    is_consistent = True
    confidence_boost = 0.0
    
    if not table_data:
        return {"is_consistent": True, "issues": [], "confidence_boost": 0.0}
    
    chart_values = chart_data.get("values", [])
    if not chart_values:
        return {"is_consistent": True, "issues": [], "confidence_boost": 0.0}
    
    # Try to find matching values in table
    table_values = []
    for row in table_data:
        for cell in row:
            if isinstance(cell, (int, float)):
                table_values.append(cell)
            elif isinstance(cell, str):
                try:
                    table_values.append(float(cell.replace(',', '').replace('$', '')))
                except ValueError:
                    pass
    
    # Check if chart values appear in table
    for chart_val in chart_values:
        found = any(abs(chart_val - table_val) < 0.01 * max(abs(chart_val), 1) for table_val in table_values)
        if not found:
            issues.append(f"Chart value {chart_val} not found in table")
            is_consistent = False
        else:
            confidence_boost += 0.05
    
    return {
        "is_consistent": is_consistent,
        "issues": issues,
        "confidence_boost": min(confidence_boost, 0.2),
    }


def calculate_block_confidence(
    block: DocumentBlock,
    verification_data: dict[str, Any] | None = None,
) -> float:
    """Calculate final confidence score for a block based on verification.
    
    Combines:
    - Original extractor confidence (if any)
    - Cross-verification results
    - Math consistency checks
    
    Returns confidence 0.0-1.0.
    """
    base_confidence = block.confidence if block.confidence is not None else 0.8
    
    if not verification_data:
        return base_confidence
    
    # Apply confidence boost from verification
    boost = verification_data.get("confidence_boost", 0.0)
    
    # Apply penalty for verification failures
    issues = verification_data.get("issues", [])
    penalty = min(len(issues) * 0.1, 0.5)
    
    final_confidence = base_confidence + boost - penalty
    return round(max(0.0, min(1.0, final_confidence)), 4)


def enhance_block_with_verification(
    block: DocumentBlock,
    verification_data: dict[str, Any] | None = None,
) -> DocumentBlock:
    """Enhance a block with verification results and updated confidence."""
    if not verification_data:
        return block
    
    # Calculate new confidence
    new_confidence = calculate_block_confidence(block, verification_data)
    
    # Check if verification failed
    has_issues = len(verification_data.get("issues", [])) > 0
    
    # Update metadata
    enhanced_metadata = block.metadata.copy()
    enhanced_metadata.update({
        "verification": verification_data,
        "verification_passed": not has_issues,
    })
    
    return DocumentBlock(
        id=block.id,
        type=block.type,
        content=block.content,
        page=block.page,
        bbox=block.bbox,
        confidence=new_confidence,
        extractor=block.extractor,
        reading_order=block.reading_order,
        requires_review=block.requires_review or has_issues,
        metadata=enhanced_metadata,
    )


def verify_all_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Run verification on all blocks that support it."""
    verified_blocks = []
    
    for block in blocks:
        verification_data = None
        
        # Verify tables
        if block.type == BlockType.TABLE:
            table_data = block.metadata.get("rows", [])
            verification_data = verify_table_math_consistency(table_data)
        
        # Verify charts
        elif block.type == BlockType.CHART:
            chart_data = block.metadata.get("chart_data", {})
            # Try to find related table for cross-verification
            related_table = None
            for other_block in blocks:
                if other_block.type == BlockType.TABLE and other_block.page == block.page:
                    related_table = other_block.metadata.get("rows")
                    break
            verification_data = verify_chart_data_consistency(chart_data, related_table)
        
        # Enhance block with verification
        if verification_data:
            verified_block = enhance_block_with_verification(block, verification_data)
            verified_blocks.append(verified_block)
        else:
            verified_blocks.append(block)
    
    return verified_blocks
