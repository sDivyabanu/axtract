"""Financial and semantic context enrichment service.

Implements:
1. Scale & Currency Detector (detect "all figures in ₹ crores", "$ in millions")
2. Time Period & Document Classification (FY2024, Q1, H1, Income Statement, Balance Sheet)
3. Footnote Linker (link superscript markers to footnote text)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from models.document import BlockType, DocumentBlock

logger = logging.getLogger(__name__)


def detect_scale_and_currency(content: str) -> dict[str, Any]:
    """Detect global scaling notes and currency in text.
    
    Patterns to detect:
    - "all figures in ₹ crores"
    - "$ in millions"
    - "values in thousands"
    - "GBP in billions"
    
    Returns detected scale and currency info.
    """
    scale_patterns = [
        (r'(?:all\s+)?(?:figures|values|amounts|numbers)\s+(?:are\s+)?(?:in\s+)?(crores|millions|billions|thousands)', 'scale'),
        (r'(?:in\s+)?(₹|\$|€|£|¥|USD|EUR|GBP|JPY|INR)\s+(?:crores|millions|billions|thousands)', 'currency_scale'),
        (r'(?:in\s+)?(crores|millions|billions|thousands)\s+(?:of\s+)?(₹|\$|€|£|¥|USD|EUR|GBP|JPY|INR)', 'scale_currency'),
    ]
    
    detected = {
        "scale": None,
        "currency": None,
        "confidence": 0.0,
    }
    
    for pattern, pattern_type in scale_patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            if pattern_type == 'scale':
                detected["scale"] = match.group(1).lower()
                detected["confidence"] = 0.8
            elif pattern_type == 'currency_scale':
                detected["currency"] = match.group(1)
                detected["scale"] = match.group(2).lower()
                detected["confidence"] = 0.9
            elif pattern_type == 'scale_currency':
                detected["scale"] = match.group(1).lower()
                detected["currency"] = match.group(2)
                detected["confidence"] = 0.9
            break
    
    return detected


def detect_time_period(content: str) -> dict[str, Any]:
    """Detect time period classification in text.
    
    Patterns to detect:
    - FY2024, FY 2024, Fiscal Year 2024
    - Q1, Q2, Q3, Q4
    - H1, H2 (Half year)
    - 2024, 2023, etc.
    
    Returns detected time period info.
    """
    year_pattern = r'(?:FY|Fiscal\s+Year)?\s*(\d{4})'
    quarter_pattern = r'Q([1-4])'
    half_pattern = r'H([12])'
    
    year_match = re.search(year_pattern, content, re.IGNORECASE)
    quarter_match = re.search(quarter_pattern, content, re.IGNORECASE)
    half_match = re.search(half_pattern, content, re.IGNORECASE)
    
    detected = {
        "year": None,
        "quarter": None,
        "half": None,
        "period_type": None,
    }
    
    if year_match:
        detected["year"] = int(year_match.group(1))
        detected["period_type"] = "annual"
    
    if quarter_match:
        detected["quarter"] = int(quarter_match.group(1))
        detected["period_type"] = "quarterly"
    
    if half_match:
        detected["half"] = int(half_match.group(1))
        detected["period_type"] = "half_yearly"
    
    return detected


def detect_document_type(content: str) -> dict[str, Any]:
    """Detect document statement type.
    
    Types to detect:
    - Income Statement
    - Balance Sheet
    - Cash Flow Statement
    - Statement of Changes in Equity
    - Profit and Loss
    
    Returns detected document type.
    """
    doc_types = [
        (r'income\s+statement', 'income_statement'),
        (r'balance\s+sheet', 'balance_sheet'),
        (r'cash\s+flow\s+statement', 'cash_flow'),
        (r'statement\s+of\s+changes\s+in\s+equity', 'equity_statement'),
        (r'profit\s+and\s+loss', 'profit_loss'),
        (r'statement\s+of\s+financial\s+position', 'balance_sheet'),
    ]
    
    detected = {
        "document_type": None,
        "confidence": 0.0,
    }
    
    for pattern, doc_type in doc_types:
        if re.search(pattern, content, re.IGNORECASE):
            detected["document_type"] = doc_type
            detected["confidence"] = 0.85
            break
    
    return detected


def extract_footnotes(blocks: list[DocumentBlock]) -> dict[str, DocumentBlock]:
    """Extract footnote blocks from document.
    
    Footnotes are typically:
    - At the bottom of pages
    - Small text
    - Numbered or lettered
    - Start with numbers or symbols
    
    Returns dict mapping footnote markers to blocks.
    """
    footnotes = {}
    
    for block in blocks:
        # Check if block looks like a footnote
        content = block.content.strip()
        
        # Footnote patterns: starts with number, letter, or symbol
        footnote_pattern = r'^([0-9]+|[a-z]+|\*|†|‡)\.?\s+'
        if re.match(footnote_pattern, content, re.IGNORECASE):
            # Extract the marker
            marker = re.match(footnote_pattern, content, re.IGNORECASE).group(1)
            footnotes[marker] = block
    
    return footnotes


def link_footnotes_to_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Link superscript footnote markers in blocks to footnote text.
    
    Patterns to find:
    - Revenue¹
    - Net Income[1]
    - EBITDA*
    
    Returns blocks with footnote links in metadata.
    """
    footnotes = extract_footnotes(blocks)
    
    linked_blocks = []
    
    for block in blocks:
        # Find footnote markers in content
        content = block.content
        footnote_markers = re.findall(r'([0-9]+|[a-z]+|\*|†|‡)(?:\s|$)', content)
        
        footnote_links = []
        for marker in footnote_markers:
            if marker in footnotes:
                footnote_links.append({
                    "marker": marker,
                    "footnote_id": footnotes[marker].id,
                    "footnote_text": footnotes[marker].content,
                })
        
        # Update metadata
        enhanced_metadata = block.metadata.copy()
        if footnote_links:
            enhanced_metadata["footnote_links"] = footnote_links
        
        linked_block = DocumentBlock(
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
        linked_blocks.append(linked_block)
    
    return linked_blocks


def enrich_block_with_context(block: DocumentBlock, document_context: dict[str, Any]) -> DocumentBlock:
    """Enrich a block with financial and semantic context.
    
    Propagates document-level context (scale, currency, time period) to blocks.
    """
    # Skip if block already has context
    if "financial_context" in block.metadata:
        return block
    
    # Detect context from block content
    scale_currency = detect_scale_and_currency(block.content)
    time_period = detect_time_period(block.content)
    doc_type = detect_document_type(block.content)
    
    # Merge with document-level context
    context = {
        "scale": scale_currency.get("scale") or document_context.get("scale"),
        "currency": scale_currency.get("currency") or document_context.get("currency"),
        "year": time_period.get("year") or document_context.get("year"),
        "quarter": time_period.get("quarter") or document_context.get("quarter"),
        "half": time_period.get("half") or document_context.get("half"),
        "period_type": time_period.get("period_type") or document_context.get("period_type"),
        "document_type": doc_type.get("document_type") or document_context.get("document_type"),
    }
    
    # Update metadata
    enhanced_metadata = block.metadata.copy()
    enhanced_metadata["financial_context"] = context
    
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


def enrich_document_with_context(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """Enrich all blocks in a document with financial and semantic context.
    
    1. Extract document-level context from headings and first paragraph
    2. Propagate context to all blocks
    3. Link footnotes to their references
    """
    if not blocks:
        return blocks
    
    # Extract document-level context from early blocks
    document_context = {}
    
    # Check first few blocks for context
    for block in blocks[:5]:
        if block.type in (BlockType.HEADING, BlockType.PARAGRAPH):
            scale_currency = detect_scale_and_currency(block.content)
            time_period = detect_time_period(block.content)
            doc_type = detect_document_type(block.content)
            
            if scale_currency.get("scale"):
                document_context["scale"] = scale_currency["scale"]
            if scale_currency.get("currency"):
                document_context["currency"] = scale_currency["currency"]
            if time_period.get("year"):
                document_context["year"] = time_period["year"]
            if time_period.get("quarter"):
                document_context["quarter"] = time_period["quarter"]
            if time_period.get("half"):
                document_context["half"] = time_period["half"]
            if time_period.get("period_type"):
                document_context["period_type"] = time_period["period_type"]
            if doc_type.get("document_type"):
                document_context["document_type"] = doc_type["document_type"]
    
    # Enrich all blocks with context
    context_enriched_blocks = [
        enrich_block_with_context(block, document_context) for block in blocks
    ]
    
    # Link footnotes
    footnote_linked_blocks = link_footnotes_to_blocks(context_enriched_blocks)
    
    return footnote_linked_blocks
