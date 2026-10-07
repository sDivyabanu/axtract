# ParseAnything

Universal document ingestion engine. Accepts documents and converts them into structured JSON
(and later Markdown), preserving page numbers, bounding boxes, confidence scores and content types.

**Current scope:** the project foundation only. The backend extracts digital text from PDFs with
PyMuPDF; the frontend is a minimal page that proves the pipeline end to end.

## Prerequisites

- Python 3.11+ (tested with 3.14)
- Node.js 20.9+ and npm (tested with Node 22)
- Git

## Project layout

```
backend/
  main.py          FastAPI app: CORS, routers, structured error handlers
  routers/         HTTP routes only (health, parse)
  services/        Orchestration: validate upload -> pick extractor -> build response
  extractors/      One module per extraction method; registry.py maps file types to extractors
  models/          Pydantic schema (document.py) and error types (errors.py)
  utils/           File helpers (safe filenames, temp upload handling)
  uploads/         Temporary upload storage (contents are git-ignored)
  requirements.txt
frontend/
  app/             Next.js App Router pages
  components/      Upload and result UI
  lib/             API client and TypeScript types mirroring the backend schema
.env.example       Documented environment variables
```

## Backend setup

Run from the repository root.

```bash
cd backend

# Create a virtual environment
python -m venv .venv

# Activate it
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# Windows (Git Bash):    source .venv/Scripts/activate
# macOS / Linux:         source .venv/bin/activate

# Install dependencies
python -m pip install -r requirements.txt

# Run the API (http://localhost:8000)
uvicorn main:app --reload
```

Interactive API docs are available at http://localhost:8000/docs while the server is running.

## Frontend setup

In a second terminal, from the repository root:

```bash
cd frontend

# Install dependencies
npm install

# Optional: override the backend URL (defaults to http://localhost:8000)
# Copy the root .env.example to frontend/.env.local and edit it if needed.

# Run the dev server (http://localhost:3000)
npm run dev
```

Other scripts: `npm run build`, `npm run start`, `npm run lint`.

## Local URLs

| Service  | URL                            |
| -------- | ------------------------------ |
| Frontend | http://localhost:3000          |
| Backend  | http://localhost:8000          |
| API docs | http://localhost:8000/docs     |

Start the backend before using the frontend. The browser calls the backend directly, and CORS
allows `http://localhost:3000` and `http://127.0.0.1:3000`.

## API

### `GET /health`

```json
{ "status": "ok" }
```

### `POST /api/parse`

Multipart form upload with a single field named `file`.

```bash
curl -F "file=@sample.pdf" http://localhost:8000/api/parse
```

Success (HTTP 200) returns a `DocumentResponse`:

```json
{
  "document_id": "e7d9...",
  "filename": "sample.pdf",
  "file_type": "pdf",
  "page_count": 2,
  "processing_time_ms": 17,
  "status": "success",
  "blocks": [
    {
      "id": "p1-b0",
      "type": "paragraph",
      "content": "ParseAnything sample heading line",
      "page": 1,
      "bbox": [72.0, 82.8, 322.8, 104.8],
      "confidence": null,
      "extractor": "pymupdf",
      "metadata": { "pymupdf_block_no": 0, "page_width": 595.0, "page_height": 842.0 }
    }
  ],
  "errors": []
}
```

- `status` is `"success"`, or `"partial"` when some pages failed (details in `errors`).
- `bbox` is `[x1, y1, x2, y2]` in PDF points with the origin at the top-left of the page. It is `null` when unavailable.
- `confidence` is `null` unless the extractor provides a real value.

Errors use one shape:

```json
{
  "status": "error",
  "error": { "code": "UNSUPPORTED_FORMAT", "message": "This file type is not supported yet." }
}
```

| HTTP | Code                  | Meaning                                              |
| ---- | --------------------- | ---------------------------------------------------- |
| 400  | `MISSING_FILE`        | No file was included in the request                  |
| 400  | `EMPTY_FILE`          | The uploaded file has zero bytes                     |
| 415  | `UNSUPPORTED_FORMAT`  | The file extension has no extractor yet (only `.pdf`) |
| 422  | `INVALID_FILE`        | The PDF could not be opened                          |
| 422  | `ENCRYPTED_FILE`      | Password-protected PDFs are not supported yet        |
| 422  | `INVALID_REQUEST`     | The multipart request is malformed                   |
| 500  | `INTERNAL_ERROR`      | Unexpected server error                              |

## Adding an extractor

1. Create `backend/extractors/<name>_extractor.py` with a class that subclasses `BaseExtractor`.
   Set `name` and `supported_extensions`, and implement `extract(file_path) -> ExtractionResult`.
2. Register an instance in `backend/extractors/registry.py`.
3. Return blocks using the shared `DocumentBlock` model. Use `None` for any value the extractor
   does not really know, such as `confidence`.

The route and service do not need to change.

## Current limitations

Not implemented yet: OCR and scanned PDFs, table, chart and equation extraction, layout detection,
reading-order reconstruction, AI/LLM/VLM integration, confidence fallback, a database, task queues,
cloud storage, and authentication. Only `.pdf` is accepted.
