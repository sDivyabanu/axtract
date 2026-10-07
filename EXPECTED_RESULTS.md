# AXTRACT test suite: expected results

Upload each file to POST /api/parse (or the site) and compare. Values below were checked against PyMuPDF `find_tables`, RapidOCR and python-docx on the generated files. Items marked **(depends on your code)** are things your docs don't pin down, so treat a mismatch there as "investigate", not automatically a bug.

**Update:** charts and equations are now really extracted (no placeholders). See `reports/after_report.md`; expected values for the charts are the ground truth listed under 04, equations under 05. Model-read LaTeX is flagged when unverified.

---

## 01_cross_page_table.pdf (cross-page merge + financial parsing)
4 pages. A 4-column table (`Line Item | FY2024 ($) | FY2023 | Change (%)`) with a repeated header on pages 1-3, then a separate 2-column calendar table on page 4.

| Page | Table rows on page (incl. repeated header) | Data rows |
|---|---|---|
| 1 | 32 | 31 (first: Product Sales - North America, last: Maintenance Contracts - North America) |
| 2 | 37 | 36 (Maintenance Contracts - Europe ... Partner Commissions - North America) |
| 3 | 13 | 12 (Partner Commissions - Europe ... TOTAL) |
| 4 | 5 | separate 2-col table (Milestone/Date), 4 data rows |

**Expected after merge**
- ONE table block spanning pages 1-3: 1 header row + **79 data rows** (78 line items + TOTAL). Repeated headers on pages 2 and 3 are skipped (header text appears once).
- Metadata: `is_cross_page_merged: true`, `merged_from_pages: [1, 2, 3]`, `merged_table_ids` with 3 ids.
- The page-4 calendar table is **NOT merged** (2 cols vs 4 cols), so a separate table block, `is_cross_page_merged` false/absent.
- Parsed values (full list in `01_expected_values.json`):

| Raw | Expected |
|---|---|
| `$690,487` | 690487 |
| `529,464` | 529464 |
| `30.4%` | 0.304 |
| `($608,508)` | -608508 (row "Service Revenue Adjustments - APAC") |
| `-222.1%` | -2.221 (plain minus percent; **depends on your code**, outside your documented rules) |
| TOTAL `$27,725,540` / `34,593,021` / `-19.9%` | 27725540 / 34593021 / -0.199 |

Negative-adjustment rows ("... Adjustments - ...") appear every 9th row, so you get parenthesised negatives throughout.

---

## 02_merged_cells_multirow_header.pdf
1 page, 6x5 grid.
- `header_row_count: 2` (header row indices `[0, 1]`).
- Merged cells: "Segment" rowspan 2 (r0,c0); "FY2024" colspan 2 (r0,c1); "FY2023" colspan 2 (r0,c3); footer note colspan 5 (r5,c0).
- PyMuPDF raw output for the header rows is `['Segment','FY2024',None,'FY2023',None]` and `[None,'H1','H2','H1','H2']`.
- Markdown should carry markers, roughly: `FY2024 <colspan:2>`, `Segment <rowspan:2>`, note row `<colspan:5>` (exact marker placement **depends on your code**).
- Parsed numbers: `1,200`→1200, `(450)`→-450, `$980`→980, `(510)`→-510, `$1,020`→1020.

## 03_financial_number_formats.pdf
Single table, 1 page; the PDF lists the raw value and the expected result side by side.

| Raw | Expected |
|---|---|
| `(1,234)` | -1234 |
| `1,234.56` | 1234.56 |
| `$1,234` | 1234 |
| `50%` | 0.5 |
| `($2,500.75)` | -2500.75 |
| `42` | 42 |
| `12.5%` | 0.125 |
| `(12.5%)` | -0.125 (edge) |
| `N/A` | unchanged text |
| `-` | unchanged / null |

Note: the columns "Raw text in PDF" and "Expected parsed value" both live in the table, so **only parse the middle column**. Column 3 contains values like `-1234` that may also get parsed; that's normal.

## 04_charts.pdf (chart detection)
3 pages, 5 images.

| Fig | Page | Aspect (w/h) | Expected |
|---|---|---|---|
| 1 Quarterly Revenue Bar Chart | 1 | 1.60 | `chart`, type `bar`, `requires_review: true` |
| 2 Monthly Active Users Line Chart | 1 | 1.78 | `chart`, type `line` |
| 3 Market Share Pie Chart | 2 | 1.00 | `chart`, type `pie` |
| 4 Ad Spend vs Sales Scatter Plot | 2 | 1.40 | `chart`, type `scatter` |
| 5 Company Banner | 3 | 6.00 | stays `figure` (outside 0.5-3.0), not flagged |

Charts are read from the images: bar/line/pie/scatter with their series values (`values_estimated` unless printed labels agree). The banner stays a `figure`.
Ground truth for when you plug in a vision LLM: bar Q1-Q4 = 120, 145, 160, 190 ($M); line Jan-Jun = 1.2, 1.5, 1.9, 2.4, 2.8, 3.5 (M users); pie Alpha 40 / Beta 30 / Gamma 20 / Other 10; scatter x=10..80 step 10, y = 25, 38, 52, 61, 79, 88, 104, 118.

## 05_equations.pdf
2 pages: one inline text line (`x^2 + y^2 = r^2`) and 4 equation images ("Equation 1" ... "Equation 4" headings).
- Ground-truth LaTeX: `E = mc^2`; `\int_0^\infty e^{-x^2}\, dx = \frac{\sqrt{\pi}}{2}`; `\sum_{n=1}^{\infty} \frac{1}{n^2} = \frac{\pi^2}{6}`; `x = \frac{-b \pm \sqrt{b^2 - 4ac}}{2a}`.
- Expected now: 5 `equation` blocks: the inline text equation (`x^{2} + y^{2} = r^{2}`) and the four images, read by the local formula model (validated + OCR cross-checked; the integral and the series sum are typically flagged for review).
- **Depends on your code:** your notes don't say what makes the PDF extractor emit a block of type `equation`. These images will most likely come out as `figure` blocks, in which case no equation enhancement runs. That would mean equation *detection* is a gap, separate from the LaTeX placeholder.

## 06_mixed_digital_scanned.pdf (adaptive routing)
- Page 1: digital. Heading "Mixed Document Test" + 3 lines, extractor `pymupdf`, `confidence: null`.
- Page 2: image-only scan, no text layer. Routed to OCR, extractor `rapidocr`, real confidences (~0.98-1.0). Expect 8 lines: "Quarterly Operations Memo", "This memo summarizes the operational results.", "Revenue increased by 12 percent.", "Customer support tickets decreased by 8 percent.", "Key Actions", "1. Hire two backend engineers", "2. Migrate the billing service", "3. Review vendor contracts".
- Page 3: digital. Heading "Conclusion" + 1 line, `pymupdf`.
- `page_count: 3`.

## 07_report.docx
- Title "Acme Quarterly Report"; headings "1. Overview", "1.1 Highlights", "2. Financial Table", "3. Chart".
- 1 paragraph, 3 bullet items, 3 numbered items.
- 1 table, 6x5, same layout as file 02 (two-row header, merged cells: Segment rowspan 2, FY2024/FY2023 colspan 2, note row colspan 5). Cells include `(450)`, `$980`, `50%`, `45%`.
- Paragraph "Figure: Quarterly Revenue Bar Chart" followed by an embedded image (bar chart, aspect 1.6).
- Closing paragraph "End of document."
- Cross-page merge does not apply (single page-level table).

## 08_deck.pptx
4 slides.
1. Title: "AXTRACT Test Deck", subtitle "Subtitle: PPTX extraction".
2. "Agenda": list of 3 (Revenue review, Regional table, Chart image).
3. "Regional Revenue": 5x4 table; rows include `$1,200`, `(450)`, `-6%`, `1,234.56`, `(1,234)`, `50%`.
4. "Quarterly Revenue Bar Chart": one picture (the bar chart image), bbox from shape coordinates.

## 09_financials.xlsx
- Sheet "Income Statement": A1:E1 merged title "Acme Corp - Income Statement (USD)"; 2-row header (A2:A3 merged "Line Item", B2:C2 "FY2024", D2:E2 "FY2023", row 3 = H1/H2/H1/H2). Data rows 4-7:
  - Product Revenue: `$1,200`, `$1,350`, `1,100`, `1,250` (text cells)
  - Cost of Goods Sold: `(450)`, `(520)`, `(480)`, `(510)` (text, expect negatives)
  - Operating Margin: `35%`, `38%`, `30%`, `33.5%` (text, expect 0.35 etc.)
  - Other Income: numeric cells 1234.56, -1234, 980, 1020 (already numbers, no conversion needed)
- Sheet "Summary": 4 rows x 2 cols: Total Customers 15230, Churn Rate 0.03, Avg Deal Size 4200.5.
- Expect 2 table blocks (one per sheet), merged-cell metadata on the first.

## 10_invoice_ocr.png / 10b_scanned_memo.png / 11_chart_image.jpg (OCR)
- **10_invoice_ocr.png**: text lines "INVOICE #1042", "Bill To: Jane Doe", "Date: 2026-09-15", then grid cells Item/Qty/Price, Widget A 2 $15.00, Widget B 5 $8.50, Gadget C 1 $120.00, and "Total: $192.50". Confidences ~0.95-1.0. OCR returns text lines/blocks; it will **not** reconstruct the grid as a table (unless you added that logic).
- **10b_scanned_memo.png**: same 8 lines as page 2 of file 06. A rare character misread (e.g. "12" as "l2") is normal OCR noise.
- **11_chart_image.jpg**: a bar chart picture; OCR returns title "Quarterly Revenue ($M)", axis labels, tick labels and bar values (120, 145, 160, 190). It is a standalone image, so chart-type detection from figure blocks likely doesn't apply.

## Error / edge cases
| File | Expected |
|---|---|
| 12_not_really_a_pdf.pdf | Plain text with a .pdf name: rejected by magic-byte check with an error response |
| 13_corrupt_truncated.pdf | First third of a valid PDF: clean error (or `partial`), no server crash |
| 14_unsupported_format.txt | Unsupported format error from the registry |

Exact error codes/messages come from `models/errors.py`.

---
## Quick pass/fail checklist
- [ ] 01: single merged table, 79 data rows, header once, pages [1,2,3]; calendar table separate
- [ ] 01: `($608,508)` is -608508; `30.4%` is 0.304
- [ ] 02/07: header_row_count 2 and colspan/rowspan metadata
- [ ] 03: all documented conversions correct
- [ ] 04: figs 1-4 become `chart` with right type, banner stays `figure`
- [ ] 05: see note about equation detection
- [ ] 06: page 2 goes through OCR, pages 1 and 3 use PyMuPDF
- [ ] 12-14: errors, not crashes
