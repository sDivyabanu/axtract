# AXTRACT

AXTRACT converts documents (PDF, DOCX, PPTX, XLSX, JPG, PNG) into structured JSON blocks and clean Markdown. Each block keeps its
type, page, normalized bounding box, reading order, the extractor that produced it, and a confidence value only when the
underlying engine reports one. Results are checked by an independent verification layer, and documents saved through the web
UI are stored encrypted on the local machine.

On top of the parser, **DealLens** provides question answering over a set of documents (a "data room") with citations,
computed numeric answers and a grounding check.

AXTRACT is a local-only application: the backend and frontend bind to loopback addresses and there is no account system.

## Key Features

- **Multi-format extraction**: PDF (digital, scanned and mixed), DOCX, PPTX, XLSX, JPG and PNG.
- **Adaptive PDF routing**: each page is analysed and sent to native text extraction, OCR or a vision model as needed.
- **Layout reconstruction**: heading detection, multi-column reading order, header/footer separation, tables (including merged
  cells), figure and chart regions, and equations as LaTeX.
- **Three PDF/image engines**: native AXTRACT (PyMuPDF + RapidOCR), Azure OpenAI `gpt-5.4-mini`, and local LightOnOCR models
  served by llama.cpp.
- **AXTRACT Verify**: an independent pass that checks completeness, content, structure and reading order and attaches a validation
  report without changing the extraction.
- **Security scanning**: active PDF content, Office macros/OLE/DDE, hidden text, Unicode tricks and dangerous links are reported
  separately from the document body.
- **Encrypted local storage**: documents saved through the web UI are stored with AES-256-GCM envelope encryption; metadata and
  history live in SQLite.
- **DealLens RAG**: hybrid retrieval, cited answers, table calculations done by code, prompt-injection quarantine, contradiction
  detection and diligence packs.
- **Batch CLI**: one command converts a folder of PDFs into one Markdown file per PDF.
- **Web UI**: upload and extraction progress, Blocks/Markdown/JSON views with source highlighting, document history, DealLens
  workspaces and a benchmark runner.

## System Architecture

```
Upload ─► validation (extension, magic bytes, size) ─► security pre-scan
        ─► extractor registry
             PDF/image: native router │ Azure gpt-5.4-mini │ LightOnOCR (local)
             DOCX / PPTX / XLSX: format-specific extractors
        ─► common DocumentBlock model
        ─► tables, charts, equations ─► reading order & layout
        ─► output sanitization ─► Markdown
        ─► AXTRACT Verify (validation report)
        ─► JSON + Markdown response
             (documents saved through the web UI: encrypted original + SQLite record)
```

- **Backend** (`backend/`): FastAPI. `services/parse_service.py` orchestrates the pipeline; `extractors/registry.py` selects the
  extractor for each file type and OCR provider.
- **Frontend** (`frontend/`): Next.js app that talks to the backend API.
- **Verification** (`backend/verify/`): integrity, source inventory, completeness, content, structure and reading-order checks,
  combined into one report. It never raises and never modifies the extraction.
- **Security** (`backend/security/`): detects PDF JavaScript/auto-actions, Office VBA/OLE/DDE, Excel formula overrides, hidden PDF
  text, hidden sheets/rows/columns, off-slide text, zero-width and bidirectional Unicode, and `javascript:`/`data:` links. Findings
  are returned in `security_findings[]` and `hidden_content[]`, never mixed into the body.
- **Storage** (`backend/db/`, `backend/crypto/`): SQLite metadata under `backend/data/local-no-auth/`; each original is encrypted
  with its own data key, wrapped by a locally generated master key.
- **DealLens RAG** (`backend/rag/`): parsed documents are chunked into a per-workspace SQLite index. Retrieval combines BM25 and
  dense embeddings (`BAAI/bge-small-en-v1.5`) with reciprocal rank fusion and a cross-encoder reranker. Numeric questions are
  planned as table operations and computed by code. A local LLM (Ollama, default `qwen3:4b-instruct`) only phrases answers from
  cited sources, and a verifier marks any number, date or entity it cannot ground. Chunks with instruction-like or hidden text
  are quarantined before they can reach a prompt. Without Ollama, answers are extractive.

## Extraction Engines

The PDF/image engine is selected with `AXTRACT_OCR_PROVIDER` (`local` by default). DOCX, PPTX and XLSX always use the native
extractors.

| Engine | Setting | What it does |
|---|---|---|
| Native AXTRACT | `AXTRACT_OCR_PROVIDER=local`, `AXTRACT_VLM_OCR=off` | PyMuPDF for digital pages (text, fonts, tables, figures); RapidOCR (ONNX Runtime) for scanned pages and images; local formula and chart readers. Fully offline. |
| Azure GPT-5.4-mini | `AXTRACT_OCR_PROVIDER=azure` | Renders each page and transcribes it with the Azure OpenAI Responses API. In `hybrid` mode, clean digital prose reuses native text and difficult pages (math, tables, columns, scans) go to the model. Includes dense-page crops, bounded rereads, header/footer separation, HTML table parsing and a per-page USD cap. |
| LightOnOCR (local) | `AXTRACT_OCR_PROVIDER=local`, `AXTRACT_VLM_OCR=scanned` or `all` | Sends selected pages to a local llama.cpp server running `LightOnOCR-3-0.8B`. Optionally, scan-like pages go to a second server running `LightOnOCR-3-4B`. If a server is unreachable, the page falls back to the native engine. |

Sending pages to Azure transmits document images and text to your Azure resource and incurs usage charges.

## Installation & Setup

Requirements: Python 3.11+ (the pinned requirements were tested on 3.14), Node.js 20+, and Git. Commands below are for PowerShell
from the repository root.

**Backend**

```powershell
py -3.14 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env      # then edit backend\.env (see below)
```

**Frontend**

```powershell
cd frontend
npm install
```

**Optional components**

- *Local LightOnOCR*: download a llama.cpp release and the GGUF weights listed in [docs/VLM_OCR.md](docs/VLM_OCR.md), then start
  the server:
  ```powershell
  llama-server.exe -m lo3-0.8b-Q8_0.gguf --mmproj lo3-0.8b-mmproj-f16.gguf --host 127.0.0.1 --port 8090 -c 16384 -ngl 99 --jinja --reasoning off -np 1
  ```
  For scan pages, start a second server with `lo3-4b-Q4_K_M.gguf` on port `8091` (`-np 1`).
- *DealLens RAG*: install Ollama and pull the model, then install the embedding package and weights:
  ```powershell
  ollama pull qwen3:4b-instruct
  backend\.venv\Scripts\python.exe -m pip install fastembed
  backend\.venv\Scripts\python.exe scripts\fetch_rag_models.py
  ```
  On macOS/Linux, `scripts/setup_rag.sh` performs these steps.
- *Scanned-page equations* (Pix2Text-MFR, offline): see [docs/SCANNED_MATH_SETUP.md](docs/SCANNED_MATH_SETUP.md).

## Environment Configuration

All backend settings are read from `backend/.env`; variables set in the shell take precedence. Never commit this file or put the
Azure key in frontend environment files.

**Local mode (required for the app)**

```env
AXTRACT_LOCAL_NO_AUTH=true
```

**Azure GPT-5.4-mini**

```env
AXTRACT_OCR_PROVIDER=azure
AXTRACT_VLM_OCR=off
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<your-api-key>
AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini
AXTRACT_AZURE_MODEL=gpt-5.4-mini
AXTRACT_AZURE_MODE=hybrid
AXTRACT_AZURE_CROPS=auto
AXTRACT_AZURE_PROMPT=accuracy
AXTRACT_AZURE_SCAN_REASONING=low
AXTRACT_AZURE_DETAIL=original
AXTRACT_AZURE_MAX_OUTPUT_TOKENS=8192
AXTRACT_AZURE_TIMEOUT_SECONDS=150
AXTRACT_AZURE_DOCUMENT_SECONDS=240
AXTRACT_AZURE_MAX_RETRIES=1
AXTRACT_AZURE_MAX_REQUESTS_PER_PAGE=3
AXTRACT_AZURE_MAX_USD_PER_PAGE=0.06
```

The endpoint may be the resource URL or its `/openai/v1/` URL. If a value is omitted, the code default applies: `cloud` mode,
crops `off`, 4096 output tokens, 90 s request timeout, 180 s document deadline and no per-page USD cap. All options are listed in
`backend/.env.example`; an optional fallback deployment (`AZURE_OPENAI_FALLBACK_DEPLOYMENT`) is used only when configured.

**Local LightOnOCR**

```env
AXTRACT_OCR_PROVIDER=local
AXTRACT_VLM_OCR=all                         # off | scanned | all
AXTRACT_VLM_URL=http://127.0.0.1:8090
AXTRACT_VLM_MODEL_ID=LightOnOCR-3-0.8B-Q8_0
# Optional second model for scanned pages:
# AXTRACT_VLM_SCAN_MODEL_ID=lo3-4b-Q4_K_M
# AXTRACT_VLM_SCAN_URL=http://127.0.0.1:8091
```

**DealLens (optional)**: `OLLAMA_URL` (default `http://127.0.0.1:11434`) and `DEALLENS_LLM_MODEL` (default `qwen3:4b-instruct`).

**Frontend**: `AXTRACT_LOCAL_NO_AUTH=true` and `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`, in the shell or in
`frontend/.env.local`.

## Usage

**Run the application** (two terminals, after starting any optional model servers):

```powershell
$env:AXTRACT_LOCAL_NO_AUTH = "true"
backend\.venv\Scripts\python.exe backend\serve_local.py
```

```powershell
cd frontend
$env:AXTRACT_LOCAL_NO_AUTH = "true"; $env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000"
npm run dev:local
```

Open http://127.0.0.1:3000. The API runs at http://127.0.0.1:8000 (interactive docs at `/docs`). Restart the backend after editing
`backend/.env`. Only loopback binds are accepted; see [docs/LOCAL_DEVELOPMENT.md](docs/LOCAL_DEVELOPMENT.md).

**Parse a file through the API**

```powershell
curl.exe -F "file=@document.pdf" http://127.0.0.1:8000/api/parse
```

**Batch PDF-to-Markdown conversion**

```powershell
backend\.venv\Scripts\python.exe scripts\axtract_batch.py <input-folder> <output-folder> --workers 4
```

- PDFs are found recursively. Each `<input-folder>\sub\name.pdf` is written to `<output-folder>\sub\name.md`; a multi-page PDF
  becomes one Markdown file.
- The engine is the one configured in `backend/.env` (Azure or local). For local LightOnOCR, start the llama.cpp server first and
  use `--workers 1`.
- Failed PDFs are listed at the end, produce no Markdown file, and make the command exit with status 1.

**Tests**

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests -q
```

## Project Structure

```
backend/
  main.py, serve_local.py   FastAPI app and the loopback-only launcher
  routers/                  API routes: parse, documents, preview, DealLens (rag), benchmark, health
  services/                 Pipeline: PDF routing, layout, tables, charts, equations, Markdown, LightOnOCR client
  extractors/               Native, Azure OCR, DOCX, PPTX and XLSX extractors and the registry
  verify/                   AXTRACT Verify validation layers
  security/                 Active-content, hidden-content, Unicode and output-safety checks
  rag/                      DealLens retrieval, answering, verification and exports
  crypto/, db/, auth/       Envelope encryption, SQLite storage, local-mode guard
  tests/                    pytest suite
frontend/                   Next.js app (app/ pages, components/, lib/ API client)
scripts/                    Batch CLI, model setup and benchmark tooling
docs/                       Detailed guides (local mode, LightOnOCR, RAG design, scanned math)
evaluation/                 Text and table evaluation scripts
```
