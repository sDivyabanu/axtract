"""Suggested questions per document, generated deterministically from its headings and tables (no LLM)."""

from __future__ import annotations

import re
from typing import Any

from rag import db, index, workspaces


def for_workspace(workspace_id: str, per_doc: int = 4) -> list[dict[str, Any]]:
    docs = {d["doc_id"]: d for d in workspaces.documents(workspace_id) if d["status"] == "ready"}
    rows = list(index.get_index(workspace_id).rows.values())
    out: list[dict[str, Any]] = []
    for doc_id, d in docs.items():
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(q: str, page: int | None, kind: str) -> None:
            if q not in seen and len(items) < per_doc:
                seen.add(q)
                items.append({"question": q, "page": page, "kind": kind})

        mine = [r for r in rows if r["doc_id"] == doc_id]
        for r in mine:
            if r["kind"] == "table" and r["table_ref"]:
                title = (r["heading_path"][-1] if r["heading_path"] else "") or r["meta"].get("statement", "")
                text = r["text"].lower()
                if re.search(r"matur", text) and re.search(r"amount|outstanding", text):
                    years = sorted(set(re.findall(r"\b(20\d{2})\b", r["text"])))
                    if years:
                        add(f"What is the total debt maturing in {years[0]}?", r["pages"][0] if r["pages"] else None, "numeric")
                    add("What is the total amount of the debt schedule?", r["pages"][0] if r["pages"] else None, "numeric")
                elif title:
                    add(f"What are the key figures in “{title}”?", r["pages"][0] if r["pages"] else None, "table")
            elif r["kind"] == "chart":
                title = r["meta"].get("chart_type") or "chart"
                first = r["text"].splitlines()[0].replace("*", "")[:70]
                add(f"What values does the {title} chart show? ({first})", r["pages"][0] if r["pages"] else None, "chart")
        for r in mine:
            if r["kind"] == "section" and r["heading_path"]:
                add(f"What does the section “{r['heading_path'][-1]}” say?", r["pages"][0] if r["pages"] else None, "section")
        if items:
            out.append({"doc_id": doc_id, "filename": d["filename"], "doc_type": d["doc_type"], "suggestions": items})
    return out
