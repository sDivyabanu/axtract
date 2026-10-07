"""Evidence Pack: a single PDF that lets a third party check an answer.

Contents: question, answer, grounding, receipts with every operand, a cropped image of each cited region,
document SHA-256 hashes, page numbers, parser/model versions, timestamp, and a SHA-256 of the pack manifest.
The SHA-256 of the finished PDF is returned in the X-Evidence-Pack-SHA256 header.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageDraw

from rag import config, db, workspaces
from services import preview_service

PARSER_VERSION = "ParseAnything 0.1.0"


def _crop(doc_id: str, page: int, bbox: list[float] | None) -> Image.Image | None:
    try:
        png = preview_service.page_png(doc_id, page, 130)
    except Exception:  # noqa: BLE001
        return None
    img = Image.open(io.BytesIO(png)).convert("RGB")
    if not bbox:
        return img.resize((img.width // 2, img.height // 2))
    w, h = img.size
    pad = 0.015
    x0, y0, x1, y1 = max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(1, bbox[2] + pad), min(1, bbox[3] + pad)
    crop = img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))
    d = ImageDraw.Draw(crop)
    d.rectangle([(bbox[0] - x0) * w, (bbox[1] - y0) * h, (bbox[2] - x0) * w, (bbox[3] - y0) * h], outline=(37, 99, 235), width=3)
    return crop


def build(answer: dict[str, Any]) -> tuple[bytes, str, str]:
    """(pdf bytes, manifest sha256, pdf sha256)."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ws = workspaces.require(answer["workspace_id"])
    docs = {d["doc_id"]: d for d in workspaces.documents(answer["workspace_id"])}
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # --- what is being evidenced
    regions: list[dict[str, Any]] = []
    for c in answer.get("citations", []):
        bb = next((b for b in c.get("bboxes", []) if b.get("bbox")), {})
        regions.append({"label": f"Citation [{c['n']}]", "doc_id": c["doc_id"], "page": bb.get("page") or (c["pages"][0] if c["pages"] else 1),
                        "bbox": bb.get("bbox"), "printed": c["printed_pages"][0] if c.get("printed_pages") else None})
    for r in answer.get("receipts", []):
        for o in r["operands"]:
            regions.append({"label": f"Operand: {o['label']} = {o['display']}", "doc_id": o["doc_id"], "page": o["page"], "bbox": o["bbox"],
                            "printed": o.get("printed_page")})
    seen, uniq = set(), []
    for rg in regions:
        key = (rg["doc_id"], rg["page"], tuple(rg["bbox"] or ()))
        if key not in seen:
            seen.add(key)
            uniq.append(rg)
    regions = uniq[:18]

    used_docs = sorted({rg["doc_id"] for rg in regions})
    manifest = {
        "answer_id": answer["answer_id"], "question": answer["question"], "answer": answer["text"], "mode": answer["mode"],
        "grounding": answer.get("grounding"), "receipts": [{"title": r["title"], "result": r["result_display"], "formula": r["formula"]} for r in answer.get("receipts", [])],
        "regions": [{"doc": d, "page": p, "bbox": b} for d, p, b in [(r["doc_id"], r["page"], r["bbox"]) for r in regions]],
        "documents": [{"filename": docs[d]["filename"], "sha256": docs[d]["sha256"]} for d in used_docs if d in docs],
        "versions": {"parser": PARSER_VERSION, "llm": answer.get("model") or "none (not generated)", "embedding": config.EMBED_MODEL, "reranker": config.RERANK_MODEL},
        "generated_at": ts,
    }
    manifest_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small"); small.fontSize = 8; small.leading = 10
    story: list[Any] = [Paragraph("Evidence Pack", styles["Title"]),
                        Paragraph(f"Data room: <b>{ws['name']}</b> &nbsp;·&nbsp; generated {ts} &nbsp;·&nbsp; answer id {answer['answer_id']}", small),
                        Spacer(1, 6 * mm), Paragraph("Question", styles["Heading3"]), Paragraph(answer["question"].replace("<", "&lt;"), styles["BodyText"]),
                        Paragraph("Answer", styles["Heading3"]),
                        Paragraph((answer["text"] or "").replace("<", "&lt;"), styles["BodyText"])]
    g = answer.get("grounding")
    story.append(Paragraph(f"Mode: <b>{answer['mode']}</b>{' · model ' + answer['model'] if answer.get('model') else ''}"
                           + (f" · grounding <b>{g['verified']}/{g['total']}</b> claims verified" if g else "")
                           + (" · <b>abstained</b>" if answer.get("abstained") else ""), small))
    for b in answer.get("badges", []):
        story.append(Paragraph(f"⚠ {b['label']}", small))

    for r in answer.get("receipts", []):
        story += [Spacer(1, 4 * mm), Paragraph(f"Number receipt: {r['title']}", styles["Heading3"]),
                  Paragraph(f"<b>{r['result_display']}</b> &nbsp; <font size=8>{r['formula']}</font>", styles["BodyText"])]
        rows = [["Operand", "Value", "Document", "Page", "Conf."]]
        for o in r["operands"]:
            rows.append([o["label"][:46], o["display"], o["filename"][:30], f"p.{o.get('printed_page') or o['page']}",
                         "n/a" if o.get("confidence") is None else f"{o['confidence']:.0%}"])
        t = Table(rows, repeatRows=1, colWidths=[62 * mm, 22 * mm, 55 * mm, 16 * mm, 14 * mm])
        t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                               ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey), ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke)]))
        story.append(t)
        for w_ in r.get("warnings", []):
            story.append(Paragraph(f"⚠ {w_}", small))

    story += [Spacer(1, 5 * mm), Paragraph("Source regions", styles["Heading3"])]
    for rg in regions:
        crop = _crop(rg["doc_id"], int(rg["page"]), rg["bbox"])
        d = docs.get(rg["doc_id"], {})
        cap = Paragraph(f"<b>{rg['label']}</b><br/>{d.get('filename', '')} · PDF page {rg['page']}" + (f" · printed page {rg['printed']}" if rg.get("printed") else ""), small)
        if crop is None:
            story.append(KeepTogether([cap, Paragraph("(region image unavailable)", small), Spacer(1, 3 * mm)]))
            continue
        buf = io.BytesIO(); crop.save(buf, "PNG"); buf.seek(0)
        iw, ih = crop.size
        wmm = min(170 * mm, iw * 0.264583 * mm / 1.0)
        scale = min(1.0, (170 * mm) / (iw * 0.75), (80 * mm) / (ih * 0.75))
        story.append(KeepTogether([cap, RLImage(buf, width=iw * 0.75 * scale, height=ih * 0.75 * scale), Spacer(1, 3 * mm)]))

    story += [Spacer(1, 4 * mm), Paragraph("Provenance", styles["Heading3"])]
    prov = [["Document", "SHA-256"]] + [[docs[d]["filename"], docs[d]["sha256"]] for d in used_docs if d in docs]
    t = Table(prov, colWidths=[60 * mm, 110 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 7), ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey)]))
    story += [t, Spacer(1, 3 * mm),
              Paragraph(f"Parser: {PARSER_VERSION} · LLM: {manifest['versions']['llm']} · embeddings: {config.EMBED_MODEL} · reranker: {config.RERANK_MODEL}", small),
              Paragraph(f"Pack manifest SHA-256: <font face='Courier' size=7>{manifest_sha}</font>", small)]

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title="DealLens Evidence Pack", author="DealLens").build(story)
    pdf = buf.getvalue()
    db.audit("evidence_pack", answer["workspace_id"], answer["answer_id"], {"manifest_sha256": manifest_sha, "pdf_bytes": len(pdf)})
    return pdf, manifest_sha, hashlib.sha256(pdf).hexdigest()
