# AXTRACT

**Universal document ingestion engine.** Accepts documents in multiple formats and converts
them into structured JSON and clean Markdown, preserving headings, paragraphs, lists, tables,
figures, page context, bounding boxes, extractor provenance, and real confidence scores.

## Supported Formats

| Format | Extractor | Capabilities |
|--------|-----------|-------------|
| PDF (digital) | PyMuPDF | Text, headings (font analysis), tables, figures, bounding boxes |
| PDF (scanned) | PyMuPDF → RapidOCR | Adaptive routing: digital pages use native extraction, scanned pages are rendered and OCR'd |
| PDF (mixed) | PyMuPDF + RapidOCR | Per-page analysis routes each page to the best extractor |
| DOCX | python-docx | Headings, paragraphs, lists, tables, embedded images |
| PPTX | python-pptx | Slide titles, body text, lists, tables, images, shape coordinates |
| XLSX | openpyxl | Sheet-level tables, headers, merged cell info, multi-sheet support |
| JPG/JPEG | RapidOCR | OCR with real confidence scores and bounding boxes |
| PNG | RapidOCR | OCR with real confidence scores and bounding boxes |

## Architecture

```
UPLOAD → FILE VALIDATION → MAGIC BYTE CHECK
  ↓
FORMAT DETECTION → EXTRACTOR REGISTRY
  ↓
ADAPTIVE ROUTING (PDF: per-page analysis → digital/scanned/mixed)
  ↓
SPECIALIZED EXTRACTORS
  ├─ PyMuPDF    (digital PDF text, tables, figures, heading detection)
  ├─ RapidOCR   (scanned PDFs, images — self-hosted, ONNX Runtime)
  ├─ python-docx (DOCX)
  ├─ python-pptx (PPTX)
  └─ openpyxl    (XLSX)
  ↓
COMMON SEMANTIC BLOCKS (DocumentBlock)
  ↓
LAYOUT ANALYSIS + READING ORDER
  ├─ Header/footer detection
  ├─ Multi-column detection
  └─ Top-to-bottom ordering per column
  ↓
MARKDOWN GENERATION
  ↓
JSON + MARKDOWN RESPONSE → FRONTEND
```

## Project Layout

```
backend/
  main.py              FastAPI app: CORS, routers, structured error handlers
  routers/             HTTP routes (health, parse)
  services/
    parse_service.py   Pipeline orchestration: validate → extract → layout → markdown
    pdf_router.py      Adaptive PDF routing (digital/scanned/mixed per page)
    layout_service.py  Reading order reconstruction, column detection, header/footer
    markdown_service.py  Ordered blocks → clean Markdown
  extractors/
    base.py            BaseExtractor contract
    registry.py        Maps file extensions to extractors
    pymupdf_extractor.py  Enhanced PDF extraction (text, tables, figures, headings)
    ocr_extractor.py   RapidOCR-based extraction for images and scanned pages
    docx_extractor.py  DOCX extraction
    pptx_extractor.py  PPTX extraction
    xlsx_extractor.py  XLSX extraction
  models/
    document.py        DocumentBlock, DocumentResponse, BlockType, BBox
    errors.py          AppError, ErrorResponse, DocumentError
  utils/
    files.py           Safe filenames, temp storage, magic byte validation
  tests/               pytest test suite
  uploads/             Temporary upload storage (git-ignored)
  requirements.txt

frontend/
  app/                 Next.js App Router
  components/
    FileDropzone.tsx   Drag-and-drop multi-format upload
    ResultView.tsx     Tabbed result viewer (Blocks, Markdown, JSON)
  lib/
    api.ts             API client
    types.ts           TypeScript types mirroring backend schema

evaluation/
  scripts/
    evaluate_text.py   Text accuracy metrics (similarity, WER, CER)
    evaluate_tables.py Table extraction accuracy
    benchmark.py       Performance benchmarking
  fixtures/            Ground truth files (add your own)
```

## Prerequisites

- Python 3.11+ (tested with 3.14)
- Node.js 20+ and npm (tested with Node 22)
- Git

## Backend Setup

```bash
cd backend
python -m venv .venv

# Activate:
# PowerShell:  .venv\Scripts\Activate.ps1
# Git Bash:    source .venv/Scripts/activate
# macOS/Linux: source .venv/bin/activate

python -m pip install -r requirements.txt
uvicorn main:app --reload
```

Runs at **http://localhost:8000** — API docs at http://localhost:8000/docs

### Dependencies

| Package | Purpose |
|---------|---------|
| fastapi + uvicorn | Web framework and ASGI server |
| python-multipart | File upload handling |
| pymupdf | PDF text, table, and figure extraction |
| rapidocr-onnxruntime | Self-hosted OCR (PaddleOCR models via ONNX Runtime) |
| python-docx | DOCX extraction |
| python-pptx | PPTX extraction |
| openpyxl | XLSX extraction |
| Pillow | Image processing for OCR |
| pytest + httpx | Testing |

**Why RapidOCR instead of PaddleOCR?** RapidOCR uses the same PaddleOCR models but runs
them via ONNX Runtime instead of PaddlePaddle, making it ~10x lighter to install, fully
self-hosted, compatible with Python 3.14, and faster to start up.

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Runs at **http://localhost:3000**

Start the backend first. Other scripts: `npm run build`, `npm run lint`.

## API

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /api/parse`

Multipart form upload with field `file`. Accepts PDF, DOCX, PPTX, XLSX, JPG, PNG.

```bash
curl -F "file=@document.pdf" http://localhost:8000/api/parse
```

Returns `DocumentResponse`:

```json
{
  "document_id": "...",
  "filename": "document.pdf",
  "file_type": "pdf",
  "page_count": 5,
  "processing_time_ms": 230,
  "status": "success",
  "blocks": [
    {
      "id": "p1-b0",
      "type": "heading",
      "content": "Chapter 1",
      "page": 1,
      "bbox": [0.12, 0.06, 0.54, 0.10],
      "confidence": null,
      "extractor": "pymupdf",
      "reading_order": 0,
      "requires_review": false,
      "metadata": { "font_size": 24.0, "is_bold": true, "route": "digital" }
    }
  ],
  "markdown": "# Chapter 1\n\n...",
  "errors": []
}
```

### Coordinate Convention

`bbox` is `[x1, y1, x2, y2]` normalized to **0.0–1.0** relative to page dimensions,
origin at top-left. Formats without spatial coordinates (DOCX) use `null`.

### Confidence Policy

- **Real confidence**: preserved from the extractor/model (e.g., RapidOCR confidence)
- **Deterministic extraction**: `confidence: null` (never fabricated)
- **Low confidence**: `requires_review: true` when confidence < 0.6
- **Unsupported content**: preserved with metadata, never hallucinated

### Error Codes

| HTTP | Code | Meaning |
|------|------|---------|
| 400 | `MISSING_FILE` | No file uploaded |
| 400 | `EMPTY_FILE` | File is empty |
| 413 | `FILE_TOO_LARGE` | Exceeds 100 MB limit |
| 415 | `UNSUPPORTED_FORMAT` | Extension not supported |
| 422 | `INVALID_FILE` | File content invalid or corrupt |
| 422 | `ENCRYPTED_FILE` | Password-protected PDF |

## Testing

```bash
cd backend
python -m pytest tests/ -v
```

Tests cover: health endpoint, digital PDF, scanned PDF, DOCX, PPTX, XLSX, JPG/PNG,
unsupported format, corrupt file, empty file, invalid magic bytes, heading detection,
normalized bounding boxes, layout ordering, multi-column detection, header/footer
classification, Markdown generation for all block types.

## Evaluation & Benchmarking

See [evaluation/README.md](evaluation/README.md) for detailed metrics documentation.

```bash
# Performance benchmark
cd backend
python ../evaluation/scripts/benchmark.py /path/to/test/files/

# Text accuracy (requires ground truth)
python ../evaluation/scripts/evaluate_text.py result.json ground_truth.txt

# Table accuracy (requires ground truth)
python ../evaluation/scripts/evaluate_tables.py result.json tables_gt.json
```

## Adding an Extractor

1. Create `backend/extractors/<name>_extractor.py` subclassing `BaseExtractor`
2. Set `name` and `supported_extensions`, implement `extract(file_path) -> ExtractionResult`
3. Register in `backend/extractors/registry.py`
4. Use `None` for any value the extractor doesn't know (confidence, bbox)

The route and service do not need to change.

## Feature Status

| Feature | Status |
|---------|--------|
| Multi-format ingestion (PDF/DOCX/PPTX/XLSX/JPG/PNG) | Implemented |
| Digital PDF text extraction | Implemented |
| PDF heading detection (font analysis) | Implemented |
| PDF table extraction | Implemented (PyMuPDF find_tables) |
| PDF figure detection | Implemented |
| Scanned PDF → OCR | Implemented (adaptive routing) |
| Mixed digital/scanned PDF | Implemented (per-page routing) |
| Image OCR (JPG/PNG) | Implemented (RapidOCR) |
| DOCX extraction | Implemented |
| PPTX extraction | Implemented |
| XLSX extraction | Implemented |
| Layout analysis | Implemented (column detection, header/footer) |
| Reading order reconstruction | Implemented |
| Markdown generation | Implemented |
| Normalized bounding boxes | Implemented (0.0–1.0) |
| Real confidence scores | Implemented (OCR) |
| Structured error handling | Implemented |
| Magic byte validation | Implemented |
| Upload size limit | Implemented (100 MB) |
| Frontend multi-format upload | Implemented |
| Frontend tabbed result view | Implemented (Blocks/Markdown/JSON) |
| Frontend table rendering | Implemented (HTML tables) |
| Automated test suite | 35 tests passing |
| Evaluation framework | Implemented (scripts, needs ground truth) |
| Performance benchmarking | Implemented |
| Chart data extraction | Detection only (no value extraction without VLM) |
| Equation → LaTeX | Detection only (pix2tex not on Python 3.14) |
| Cross-page table merging | Not implemented (conservative: separate tables) |
| Advanced layout model | Heuristic only (no ML layout model) |
| PDF provenance viewer | Bbox data available, no visual overlay yet |
| Authentication | Not implemented (not needed for baseline) |
| Database | Not implemented |
| Task queue | Not implemented |
| Cloud storage | Not implemented |
| Chatbot / LLM integration | Not implemented (future feature) |

---

## Charts, equations and previews (local, no network at runtime)

**Setup (one time)**

```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd .. && backend/.venv/bin/python scripts/fetch_models.py   # formula weights, checksummed
# LibreOffice is needed only to preview DOCX/PPTX/XLSX (and EMF/WMF pictures):
#   macOS: download from libreoffice.org, or put `soffice` on PATH
```

Without the formula weights the server still runs: formula regions are flagged
`formula_model_unavailable`. Without LibreOffice, Office previews are reported in the optional
`preview_error` field; the parse itself still succeeds.

**How regions are read** (`services/region_router.py`)

| Region | Source | Method | Values |
|---|---|---|---|
| Office native chart | `word|ppt/charts/chartN.xml` | cached series in the XML | exact |
| PDF vector chart | drawing primitives (pdfplumber) | axis ticks → values | exact |
| Chart in a picture (PDF/DOCX/PPTX/JPG/PNG) | raster | OpenCV + OCR tick calibration | `values_estimated` unless printed labels agree |
| Word/PowerPoint equation | OMML | built-in OMML→LaTeX | exact |
| Equation image / scanned formula | crop | local ONNX formula model, validated + OCR cross-check | confidence ≤ 0.75, flagged when unverified |
| Inline text math (`x^2 + y^2 = r^2`) | text layer | deterministic conversion | validated |

Nothing is invented: unreadable values are `null`, unverified LaTeX is flagged
(`requires_review`, `metadata.flags`), and charts whose printed numbers contradict the
measurement are flagged `data_labels_disagree_with_measurement`.

**Optional response fields added** (schema otherwise unchanged): `preview_available`,
`preview_pages`, `preview_error`. Block `metadata` gains `preview` (page + bbox in the preview
pages, Office formats), `chart_data`, `latex`, `flags`, `needs_review`, `markdown`, `page_size_pt`.

**Components and licences**

| Component | Used for | Licence |
|---|---|---|
| pypdfium2 | page rendering | BSD-3 / Apache-2.0 |
| pdfplumber | PDF vector chart primitives | MIT |
| rapid-layout (+ bundled CDLA ONNX layout model) | formula/figure regions | Apache-2.0 (model weights: verify before commercial shipping) |
| rapid-latex-ocr (pix2tex ONNX export) | image → LaTeX | Apache-2.0 code; pix2tex MIT; weights: verify |
| latex2mathml | LaTeX syntax check | MIT |
| defusedxml | safe parsing of chart parts | PSF |
| OpenCV (via rapidocr) | chart geometry | Apache-2.0 |
| KaTeX (frontend) | equation rendering | MIT |
| LibreOffice (external, optional) | Office → PDF preview | MPL-2.0 |
| **PyMuPDF** | PDF text/tables (existing) | **AGPL-3.0: replace or license before commercial distribution** |

Tests: `cd backend && .venv/bin/python -m pytest` (LibreOffice / formula tests skip if absent).
Reports: `backend/.venv/bin/python scripts/run_samples.py --tag after` → `reports/after_report.md`
and annotated pages in `reports/previews/`.
