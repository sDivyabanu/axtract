"""Generic page/markup helpers used around the Azure transcription."""
from PIL import Image, ImageDraw

from extractors.azure_ocr_postprocess import (furniture_in_margin, native_coverage, prose_superscripts, repair_tables,
                                              split_furniture, spread_split)


def _text_page(width, height, columns):
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for x0, x1 in columns:
        for y in range(int(height * .1), int(height * .9), 28):
            draw.rectangle([x0, y, x1, y + 9], fill="black")  # a line of "writing"
    return image


def test_spread_split_finds_the_gutter_of_a_two_page_spread():
    image = _text_page(2400, 1800, [(120, 1110), (1290, 2280)])
    left, right = spread_split(image)
    assert left[0] == 0 and right[2] == 2400
    assert 1110 < left[2] <= 1320 and 1080 <= right[0] < 1290


def test_spread_split_leaves_single_pages_whole():
    assert spread_split(_text_page(2400, 1800, [(120, 2280)])) is None       # one wide page of text
    assert spread_split(_text_page(1700, 2200, [(100, 780), (920, 1600)])) is None  # portrait two-column page
    assert spread_split(_text_page(2400, 1800, [(120, 1110)])) is None        # one page with a blank facing page


def test_split_furniture_separates_labelled_margins_and_keeps_stray_tags_text():
    text = "<page-header>Journal of Things 12 (2020)</page-header>\n# Title\n\nBody text.\n<page-footer>7</page-footer>"
    body, headers, footers = split_furniture(text)
    assert body == "# Title\n\nBody text."
    assert headers == ["Journal of Things 12 (2020)"] and footers == ["7"]
    assert split_furniture("<page-header>unclosed header\nBody")[0] == "unclosed header\nBody"


def test_furniture_label_is_overruled_by_text_layer_position():
    lines = [(.03, "Journal of Things 12 (2020)"), (.5, "Results of the main experiment are shown here")]
    assert furniture_in_margin("Journal of Things 12 (2020)", lines)
    assert not furniture_in_margin("Results of the main experiment are shown here", lines)
    assert furniture_in_margin("anything", [])  # no text layer (a scan): the label stands


def test_repair_tables_converts_tab_grids_and_leaves_math_and_prose():
    text = "Figure 1\naccuracy\t95%\nprecision\t96.15%\nMCC\t0.9003\nCaption text."
    out = repair_tables(text)
    assert "| accuracy | 95% |\n| --- | --- |\n| precision | 96.15% |\n| MCC | 0.9003 |" in out
    assert out.startswith("Figure 1\n\n|") and out.endswith("|\n\nCaption text.")
    math = "$$\na\tb\nc\td\n$$"
    assert repair_tables(math) == math
    assert repair_tables("one\ttwo") == "one\ttwo"  # a single line is not a grid


def test_prose_superscripts_only_rewrites_bare_superscripts():
    assert prose_superscripts("BMI (kg/m$^2$) at 25$^\\circ$C, note$^{12}$") == "BMI (kg/m²) at 25°C, note¹²"
    assert prose_superscripts("$x^2$ and $$^2$$ and $a^{b}$") == "$x^2$ and $$^2$$ and $a^{b}$"


def test_native_coverage_reports_omitted_text_layer_lines():
    lines = [(.1, "The first paragraph describes the experimental design"),
             (.5, "Neointimal healing reduces the volume of acute malapposition"),
             (.9, "12")]
    coverage, words, missing = native_coverage("The first paragraph describes the experimental design.", lines)
    assert missing == ["Neointimal healing reduces the volume of acute malapposition"]
    assert words == 14 and coverage < .6
    assert native_coverage("anything", [])[0] == 1.0


def test_margin_furniture_needs_folio_or_text_layer_evidence():
    from extractors.azure_ocr_postprocess import margin_furniture
    body, headers, footers = margin_furniture("31\n\nDear Sir,\n\nThe body of the letter.\n\nPage 2 of 9", [])
    assert (body, headers, footers) == ("Dear Sir,\n\nThe body of the letter.", ["31"], ["Page 2 of 9"])
    lines = [(.03, "Annual Report 2021 Section Two"), (.5, "Annual Report 2021 Section Two")]
    assert margin_furniture("Annual Report 2021 Section Two\n\nBody.", lines)[1] == ["Annual Report 2021 Section Two"]
    assert margin_furniture("Annual Report 2021 Section Two\n\nBody.", lines[1:])[1] == []  # placed in the body
    assert margin_furniture("12", [])[0] == "12"  # never empties a page
    assert margin_furniture("# 3\n\nBody", [])[1] == []  # a heading is not a folio


# ── extractor integration (mocked API, no network) ──────────────────────────

from tests.test_azure_ocr import azure_env, complete, mock_api  # noqa: E402,F401 - fixture reused


def _png(tmp_path):
    from PIL import Image as _Image
    path = tmp_path / "page.png"
    _Image.new("RGB", (800, 1000), "white").save(path)
    return path


def test_cloud_page_becomes_header_body_table_and_footer_blocks(monkeypatch, tmp_path):
    from extractors.azure_ocr_extractor import AzureOCRExtractor
    from models.document import BlockType
    from services.markdown_service import blocks_to_markdown
    from services.layout_service import assign_reading_order
    text = ("<page-header>Journal of Tests 4 (2021)</page-header>\n\nIntro text kg/m$^2$.\n\n"
            "<table><tr><th colspan=\"2\">Group</th></tr><tr><td>a</td><td>1</td></tr></table>\n\nAfter <sup>2</sup>.\n\n"
            "<page-footer>17</page-footer>")
    mock_api(monkeypatch, complete(text))
    blocks = AzureOCRExtractor().extract(_png(tmp_path)).blocks
    assert [b.type for b in blocks] == [BlockType.HEADER, BlockType.PARAGRAPH, BlockType.TABLE, BlockType.PARAGRAPH,
                                        BlockType.FOOTER]
    assert blocks[2].metadata["rows"] == [["Group", ""], ["a", "1"]]
    md = blocks_to_markdown(assign_reading_order(blocks))
    assert "Journal of Tests" not in md and "17" not in md
    assert "Intro text kg/m²." in md and "After ²." in md and 'colspan="2"' in md and "&lt;" not in md
    assert sum(1 for b in blocks if b.metadata.get("cloud_transcription")) == 1  # usage is reported once per page


def test_content_filtered_page_keeps_partial_text_after_retry(monkeypatch, tmp_path):
    from extractors.azure_ocr_extractor import AzureOCRExtractor
    monkeypatch.setenv("AXTRACT_AZURE_MAX_RETRIES", "1")
    monkeypatch.setattr("extractors.azure_ocr_extractor.time.sleep", lambda _: None)
    payload = dict(complete("First part of the page"), status="incomplete", incomplete_details={"reason": "content_filter"})
    calls = mock_api(monkeypatch, payload)
    result = AzureOCRExtractor().extract(_png(tmp_path))
    assert len(calls) == 2  # filtering is retried once
    body = [b for b in result.blocks if b.metadata.get("cloud_transcription")][0]
    assert body.content == "First part of the page" and body.metadata["fallback_reason"] == "content_filter_partial"
    assert body.requires_review
