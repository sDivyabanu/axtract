# AXTRACT Verify benchmark

> Fault injection below measures whether Verify notices corruptions injected on purpose into generated fixtures.
> It is **not** real-world accuracy and **not** a real silent-loss rate.

## Real documents (`sample_files/`)

| file | status | units | verified | not verifiable | review | extract ms | verify ms | blocking issues |
|---|---|---|---|---|---|---|---|---|
| 01_cross_page_table.pdf | verified | 4 | 4 | 0 | 0 | 1003 | 290 | - |
| 02_merged_cells_multirow_header.pdf | verified | 1 | 1 | 0 | 0 | 112 | 22 | - |
| 03_financial_number_formats.pdf | verified | 1 | 1 | 0 | 0 | 95 | 25 | - |
| 04_charts.pdf | verified | 3 | 3 | 0 | 0 | 8110 | 21 | - |
| 05_equations.pdf | not_verifiable | 2 | 1 | 1 | 0 | 3361 | 14 | - |
| 06_mixed_digital_scanned.pdf | not_verifiable | 3 | 2 | 1 | 0 | 2489 | 14 | - |
| 07_report.docx | verified | 1 | 1 | 0 | 0 | 964 | 26 | - |
| 08_deck.pptx | verified | 4 | 4 | 0 | 0 | 932 | 6 | - |
| 09_financials.xlsx | verified | 2 | 2 | 0 | 0 | 85 | 5 | - |
| 10_invoice_ocr.png | not_verifiable | 1 | 0 | 1 | 0 | 1580 | 1 | - |
| 10b_scanned_memo.png | not_verifiable | 1 | 0 | 1 | 0 | 4306 | 1 | - |
| 11_chart_image.jpg | review_required | 1 | 0 | 0 | 1 | 1441 | 2 | extraction_integrity:extractor_flagged_blocks |

Total: extraction 24478 ms, validation 427 ms (1.7% overhead).

## Fault injection (fixtures)

| format | injected | detected | right layer + code | missed |
|---|---|---|---|---|
| docx | 18 | 18 | 18 | 0 |
| pptx | 11 | 11 | 11 | 0 |
| xlsx | 14 | 14 | 14 | 0 |
| pdf_text | 6 | 6 | 6 | 0 |
| pdf_table | 4 | 4 | 4 | 0 |
| **total** | 53 | 53 | 53 | 0 |
