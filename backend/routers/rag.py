"""DealLens API: workspaces (data rooms), documents, grounded Q&A over SSE."""

from __future__ import annotations

import json
from typing import Any, Iterator

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from models.errors import AppError
from rag import config, db, index, ingest, llm, qa, workspaces
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


@router.get("/rag/status")
def rag_status() -> dict[str, Any]:
    st = llm.status(force=True)
    return {"llm": {"available": st["available"], "model": config.LLM_MODEL, "reason": st["reason"]},
            "mode": "llm" if st["available"] else "extractive", "embed_model": config.EMBED_MODEL,
            "rerank_model": config.RERANK_MODEL}
