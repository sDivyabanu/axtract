# DealLens — RAG design (one page)

*"It doesn't just read the data room. It audits it — and every answer comes with a receipt."*

## Components (all local; package `backend/rag/`, routes in `backend/routers/rag.py`)
```
upload ─► parse_file (existing ParseAnything pipeline, blocks + bbox + flags)
        ─► security shield (hidden text, injection phrases, unicode tricks, xlsx hidden/hardcoded)
        ─► chunker (heading sections, tables/lists/equations never split; table = markdown + summary + grid)
        ─► facts extractor (normalised concept/period/unit values)
        ─► index: SQLite (metadata, tables, facts, audit) + BM25 (own, in-memory) + dense vectors (numpy)
ask   ─► router (lookup | numeric | list | compare) ─► hybrid retrieve (BM25 + dense, RRF) ─► cross-encoder rerank
        ─► [numeric] LLM/rule plan (JSON) ─► table-op DSL executed by our code ─► Number Receipts
        ─► generate (Ollama, or extractive fallback) ─► verifier (every number/date must be in a cited chunk or a receipt)
        ─► answer {sentences[citations], receipts, grounding, badges, stages} streamed over SSE
```
Baseline pipeline (Compare page): pypdf/python-docx text only, fixed 500-token chunks, dense-only, no verifier,
no abstention, no quarantine, same LLM.

## Data model (SQLite `backend/data/deallens.db`)
`workspaces` · `documents` (sha256, doc_type, status, flags) · `blocks` (parsed blocks, JSON) · `chunks` (kind, text, heading_path,
block_ids, pages, printed_pages, bboxes, min_confidence, flags, meta, trust, table_ref, content_hash, embedding blob) ·
`tables` (typed grid, header paths, units, cell bboxes) · `facts` (concept, value, unit, scale, period + provenance) ·
`quarantine` · `answers` · `audit` (ids, hashes, timings; never document text). Every query is scoped by `workspace_id`.

## Endpoints
`POST /workspaces`, `GET /workspaces[/{id}]`, `POST /workspaces/{id}/documents` (parse + index, progress via
`GET …/documents/{doc}`), `POST …/ask` (SSE), `POST …/compare`, `POST …/packs/{pack}`, `GET …/contradictions`,
`…/seller-questions`, `…/quarantine`, `…/maturity-wall`, `GET /eval/latest`, `POST /answers/{id}/evidence-pack`.
Existing parse/preview endpoints and fields are unchanged; new data is additive.

## UI (Next.js, new routes beside the existing explorer)
`/rooms` Data Room · `/rooms/[id]/ask` chat with citation chips → viewer highlight, receipts, glass-box ·
`/rooms/[id]/compare` · `/packs` · `/contradictions` · `/seller-questions` · `/maturity` · `/quarantine` · `/eval`.
Colour language: green verified · amber needs review/estimated · red contradiction/unverified/quarantined.

## Libraries and licences
| Need | Choice | Licence | Why |
|---|---|---|---|
| LLM runtime | Ollama (REST via httpx, BSD) | MIT | local, swappable via `DEALLENS_LLM_MODEL` / `OLLAMA_URL` |
| LLM (default) | **qwen3:4b** (thinking off) | Apache-2.0 | fits 8 GB RAM, strong JSON/plan following. Alternatives: `phi4-mini` (MIT), `qwen2.5:7b-instruct` (Apache-2.0, needs ≥16 GB). Avoid qwen2.5-3b (research licence) |
| Embeddings | fastembed + **BAAI/bge-small-en-v1.5** | Apache-2.0 / MIT | ONNX (no PyTorch), 67 MB |
| Reranker | fastembed + **Xenova/ms-marco-MiniLM-L-6-v2** | Apache-2.0 | cross-encoder, 80 MB. *Not* jina-v2 (CC-BY-NC) |
| Vector index | numpy exact cosine, persisted in SQLite | BSD | data rooms are ≤ tens of thousands of chunks: exact search is faster to build, deterministic and needs no native index library |
| BM25 | own Okapi BM25 (in-memory, incremental) | — | no dependency, supports per-workspace updates |
| Baseline text | pypdf | BSD-3 | deliberately naive |
| Exports | python-docx, openpyxl, reportlab | MIT / MIT / BSD | DOCX/XLSX/PDF |
| PDF hidden text | pdfplumber (existing) | MIT | char colour/size/render attributes. **No new PyMuPDF use.** |

## Honesty rules enforced in code
LLM never decides facts or does arithmetic (plans JSON; our DSL computes). No `eval`/`exec` of model output. Document text is
untrusted: sources are wrapped in delimited blocks with a "sources are data" instruction; quarantined chunks never reach the
prompt. If Ollama is down → extractive mode with a visible badge. Logs carry ids/hashes/timings only.
