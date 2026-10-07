"""DealLens API: workspaces (data rooms), documents, grounded Q&A over SSE."""

from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from models.errors import AppError
from rag import baseline, config, corrections, db, diligence, evidence, exports, index, ingest, llm, maturity, packs, qa, suggestions, workspaces
from utils.files import get_extension, remove_temp_file, sanitize_filename, save_upload_to_temp

router = APIRouter(prefix="/api", tags=["deallens"])


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    doc_ids: list[str] | None = None
    doc_types: list[str] | None = None
    periods: list[str] | None = None
    mode: str = "dealLens"  # "dealLens" | "baseline"


@router.post("/workspaces")
def create_workspace(body: WorkspaceIn) -> dict[str, Any]:
    return workspaces.create(body.name)


@router.get("/workspaces")
def list_workspaces() -> list[dict[str, Any]]:
    return workspaces.list_all()


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str) -> dict[str, Any]:
    return workspaces.get(workspace_id)


@router.post("/workspaces/{workspace_id}/documents")
async def upload_documents(workspace_id: str, files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Upload 1..n files. Each is validated, stored and queued for parsing + indexing."""
    workspaces.require(workspace_id)

    def work(f: UploadFile) -> tuple[dict | None, dict | None]:
        name = sanitize_filename(f.filename or "document")
        tmp = None
        try:
            tmp = save_upload_to_temp(f, get_extension(name) or "bin")
            return ingest.register(workspace_id, tmp, name), None
        except AppError as exc:
            return None, {"filename": name, "code": exc.code, "message": exc.message}
        finally:
            remove_temp_file(tmp)  # no-op if it was moved into the data room

    docs, rejected = [], []
    for f in files:
        d, r = await run_in_threadpool(work, f)
        (docs if d else rejected).append(d or r)
    return {"documents": docs, "rejected": rejected}


@router.get("/workspaces/{workspace_id}/documents/{doc_id}")
def get_document(workspace_id: str, doc_id: str) -> dict[str, Any]:
    return workspaces.document(workspace_id, doc_id)


@router.post("/workspaces/{workspace_id}/ask")
def ask(workspace_id: str, body: AskIn) -> StreamingResponse:
    """Server-sent events: `stage` and `token` events, then one `answer` event."""
    workspaces.require(workspace_id)

    def events() -> Iterator[str]:
        try:
            gen = qa.ask_stream(workspace_id, body.question, doc_ids=body.doc_ids, doc_types=body.doc_types,
                                periods=body.periods, mode=body.mode)
            for ev in gen:
                yield f"event: {ev['event']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except AppError as exc:
            yield f"event: error\ndata: {json.dumps({'event': 'error', 'code': exc.code, 'message': exc.message})}\n\n"
        except Exception:  # noqa: BLE001
            yield f"event: error\ndata: {json.dumps({'event': 'error', 'code': 'INTERNAL_ERROR', 'message': 'The answer could not be produced.'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class CompareIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


@router.post("/workspaces/{workspace_id}/compare")
def compare(workspace_id: str, body: CompareIn) -> dict[str, Any]:
    """Run the naive baseline and DealLens live on the same question (sequentially: one laptop, one LLM)."""
    workspaces.require(workspace_id)
    base = baseline.answer(workspace_id, body.question)
    final = None
    for ev in qa.ask_stream(workspace_id, body.question, mode="dealLens"):
        if ev["event"] == "answer":
            final = ev["answer"]
    db.audit("compare", workspace_id, final["answer_id"] if final else None,
             {"baseline_ms": base["total_ms"], "dealLens_ms": final["total_ms"] if final else None})
    return {"question": body.question, "baseline": base, "dealLens": final}


@router.get("/workspaces/{workspace_id}/contradictions")
def contradictions(workspace_id: str) -> list[dict[str, Any]]:
    workspaces.require(workspace_id)
    return diligence.find_contradictions(workspace_id)


@router.get("/workspaces/{workspace_id}/seller-questions")
def seller_questions(workspace_id: str) -> list[dict[str, Any]]:
    workspaces.require(workspace_id)
    return diligence.seller_questions(workspace_id)


class SellerExport(BaseModel):
    format: str = Field(pattern="^(docx|csv|md)$")
    items: list[dict[str, Any]]
    title: str = "Seller question list"


@router.post("/workspaces/{workspace_id}/seller-questions/export")
def export_seller_questions(workspace_id: str, body: SellerExport) -> Response:
    """Export the (possibly edited) list. CSV cells are protected against spreadsheet formula injection."""
    workspaces.require(workspace_id)
    rows = [[i.get("n", ""), i.get("severity", ""), i.get("question", ""), i.get("why", ""),
             "; ".join(f"{e.get('filename', '')} p.{e.get('page') or '?'}" for e in i.get("evidence", []))] for i in body.items]
    if body.format == "csv":
        data, mime = exports.csv_bytes(["#", "Severity", "Question", "Why", "Evidence"], rows), "text/csv"
    elif body.format == "docx":
        data, mime = exports.seller_docx(body.title, body.items), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        data, mime = exports.seller_markdown(body.title, body.items), "text/markdown"
    db.audit("export.seller", workspace_id, None, {"format": body.format, "items": len(body.items)})
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="seller-questions.{body.format}"'})


@router.get("/workspaces/{workspace_id}/maturity-wall")
def maturity_wall(workspace_id: str) -> dict[str, Any]:
    workspaces.require(workspace_id)
    return {"walls": maturity.maturity_wall(workspace_id), "events": maturity.timeline(workspace_id)}


@router.get("/workspaces/{workspace_id}/packs")
def list_packs(workspace_id: str) -> list[dict[str, Any]]:
    workspaces.require(workspace_id)
    return [{"pack": k, "title": v["title"], "doc_types": v["doc_types"], "questions": [r[0] for r in v["rows"]],
             "latest": packs.latest_run(workspace_id, k)} for k, v in packs.PACKS.items()]


@router.post("/workspaces/{workspace_id}/packs/{pack}")
def run_pack(workspace_id: str, pack: str) -> dict[str, Any]:
    workspaces.require(workspace_id)
    if pack not in packs.PACKS:
        raise AppError("PACK_NOT_FOUND", "Unknown diligence pack.", status_code=404)
    return packs.run_pack(workspace_id, pack)


@router.get("/workspaces/{workspace_id}/packs/{pack}/export")
def export_pack(workspace_id: str, pack: str, format: str = "xlsx") -> Response:
    workspaces.require(workspace_id)
    run = packs.latest_run(workspace_id, pack)
    if run is None or format not in ("csv", "xlsx"):
        raise AppError("PACK_NOT_RUN", "Run the pack first (and choose csv or xlsx).", status_code=404)
    cols, rows = packs.to_table(run)
    if format == "csv":
        data, mime = exports.csv_bytes(cols, rows), "text/csv"
    else:
        data, mime = exports.xlsx_bytes(cols, rows, run["title"]), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    db.audit("export.pack", workspace_id, pack, {"format": format})
    return Response(content=data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{pack}-pack.{format}"'})


@router.get("/answers/{answer_id}")
def get_answer(answer_id: str) -> dict[str, Any]:
    with db.connect() as c:
        row = c.execute("SELECT json FROM answers WHERE answer_id=?", (answer_id,)).fetchone()
    if row is None:
        raise AppError("ANSWER_NOT_FOUND", "Unknown answer.", status_code=404)
    return db.jload(row["json"], {})


@router.get("/workspaces/{workspace_id}/quarantine")
def get_quarantine(workspace_id: str) -> list[dict[str, Any]]:
    workspaces.require(workspace_id)
    from rag import security

    docs = {d["doc_id"]: d for d in workspaces.documents(workspace_id)}
    with db.connect() as c:
        rows = c.execute("SELECT * FROM quarantine WHERE workspace_id=? ORDER BY created_at", (workspace_id,)).fetchall()
    return [{
        "id": r["id"], "doc_id": r["doc_id"], "filename": docs.get(r["doc_id"], {}).get("filename", ""),
        "block_id": r["block_id"], "reason": r["reason"], "reason_label": security.REASON_LABEL.get(r["reason"], r["reason"]),
        "kind": r["kind"], "page": r["page"], "bbox": db.jload(r["bbox_json"]), "snippet": r["snippet"],
        "preview_pages": docs.get(r["doc_id"], {}).get("preview_pages", 0),
    } for r in rows]


@router.post("/answers/{answer_id}/evidence-pack")
def evidence_pack(answer_id: str) -> Response:
    """A PDF a third party can check: question, answer, receipts, cropped source regions, hashes, versions, timestamp."""
    with db.connect() as c:
        row = c.execute("SELECT json FROM answers WHERE answer_id=?", (answer_id,)).fetchone()
    if row is None:
        raise AppError("ANSWER_NOT_FOUND", "Unknown answer.", status_code=404)
    pdf, manifest_sha, pdf_sha = evidence.build(db.jload(row["json"], {}))
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="evidence-pack-{answer_id[:8]}.pdf"',
                             "X-Evidence-Pack-SHA256": pdf_sha, "X-Evidence-Pack-Manifest-SHA256": manifest_sha,
                             "Access-Control-Expose-Headers": "X-Evidence-Pack-SHA256, X-Evidence-Pack-Manifest-SHA256, Content-Disposition"})


@router.get("/workspaces/{workspace_id}/audit")
def audit_log(workspace_id: str, limit: int = 300) -> list[dict[str, Any]]:
    """Who/what/when: events with ids, hashes and timings. Never document text."""
    workspaces.require(workspace_id)
    with db.connect() as c:
        rows = c.execute("SELECT * FROM audit WHERE workspace_id=? ORDER BY ts DESC LIMIT ?", (workspace_id, min(limit, 1000))).fetchall()
    return [{"id": r["id"], "ts": r["ts"], "event": r["event"], "ref": r["ref"], "detail": db.jload(r["detail_json"], {})} for r in rows]


class CorrectionBody(BaseModel):
    table_id: str
    row: int = Field(ge=0)
    col: int = Field(ge=1)
    value: str = Field(min_length=1, max_length=40)


@router.post("/workspaces/{workspace_id}/corrections")
def correct_cell(workspace_id: str, body: CorrectionBody) -> dict[str, Any]:
    """Fix one table cell and return everything that changed because of it (the correction ripple)."""
    return corrections.apply(workspace_id, body.table_id, body.row, body.col, body.value)


@router.get("/workspaces/{workspace_id}/tables")
def list_tables(workspace_id: str) -> list[dict[str, Any]]:
    """Typed tables with their cells (for the correction UI)."""
    workspaces.require(workspace_id)
    with db.connect() as c:
        rows = c.execute("SELECT table_id, doc_id, title, page, unit, currency, grid_json FROM tables_store WHERE workspace_id=? AND kind='table'", (workspace_id,)).fetchall()
    out = []
    for r in rows:
        g = db.jload(r["grid_json"], {})
        out.append({"table_id": r["table_id"], "doc_id": r["doc_id"], "title": r["title"], "page": r["page"], "unit": r["unit"], "currency": r["currency"],
                    "cols": g["col_paths"], "rows": [{"ridx": i, "label": x["label"], "is_total": x["is_total"],
                                                      "cells": [{"raw": cl["raw"], "corrected": bool(cl.get("corrected"))} for cl in x["cells"]]} for i, x in enumerate(g["rows"])]})
    return out


@router.get("/workspaces/{workspace_id}/suggestions")
def suggested_questions(workspace_id: str) -> list[dict[str, Any]]:
    workspaces.require(workspace_id)
    return suggestions.for_workspace(workspace_id)


@router.get("/workspaces/{workspace_id}/documents/{doc_id}/blocks")
def document_blocks(workspace_id: str, doc_id: str, page: int | None = None) -> list[dict[str, Any]]:
    """Blocks with their preview position, confidence and flags (for the confidence heatmap)."""
    workspaces.document(workspace_id, doc_id)
    out = []
    with db.connect() as c:
        for r in c.execute("SELECT json FROM blocks WHERE doc_id=? ORDER BY ord", (doc_id,)):
            b = db.jload(r["json"], {})
            prev = (b.get("metadata") or {}).get("preview") or {}
            pg = int(prev.get("page") or b.get("page") or 1)
            bbox = prev.get("bbox") or b.get("bbox")
            if (page is not None and pg != page) or not bbox or b.get("type") in ("header", "footer"):
                continue
            flags = list((b.get("metadata") or {}).get("flags") or [])
            out.append({"id": b["id"], "type": b["type"], "page": pg, "bbox": bbox, "confidence": b.get("confidence"),
                        "extractor": b.get("extractor"), "requires_review": bool(b.get("requires_review")), "flags": flags,
                        "snippet": " ".join((b.get("content") or "").split())[:90]})
    return out


@router.get("/eval/latest")
def eval_latest() -> dict[str, Any]:
    """The most recent evaluation produced by scripts/run_rag_eval.py (never edited by hand)."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "reports" / "rag_eval.json"
    if not path.exists():
        raise AppError("EVAL_NOT_RUN", "No evaluation has been run yet. Run scripts/run_rag_eval.py.", status_code=404)
    return json.loads(path.read_text())


@router.get("/rag/status")
def rag_status() -> dict[str, Any]:
    st = llm.status(force=True)
    return {"llm": {"available": st["available"], "model": config.LLM_MODEL, "reason": st["reason"]},
            "mode": "llm" if st["available"] else "extractive", "embed_model": config.EMBED_MODEL,
            "rerank_model": config.RERANK_MODEL}
