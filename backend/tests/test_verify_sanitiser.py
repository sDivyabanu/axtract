"""Verify must read the output the way the output sanitiser meant it: HTML escaping is reversible
encoding, not new content, but removed script content is still missing."""

from __future__ import annotations


from security.output_safety import sanitise_block_text
from verify.content import out_text
from models.document import BlockType, DocumentBlock


def _block(text: str) -> DocumentBlock:
    return DocumentBlock(id="b1", type=BlockType.PARAGRAPH, content=sanitise_block_text(text), page=1, extractor="test")


def test_escaped_apostrophes_and_ampersands_compare_equal_to_the_source():
    original = "It's R&D's \"best\" quarter <ok>"
    assert "&" in _block(original).content  # the sanitiser really did escape something
    assert out_text(_block(original)) == original


def test_removed_markup_is_still_absent_not_restored():
    assert "alert" not in out_text(_block("<script>alert(1)</script>Hello"))
