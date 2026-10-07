"""Diligence intelligence: contradictions across documents, table totals that do not add up,
references to schedules that were never provided, and the auto-drafted seller question list.

Everything here is computed from the fact store / table store / index, never from the LLM, and every
item links back to document + page + box.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

from rag import db, facts as F, index, meta as M, workspaces

_CUR = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
_ABBR = {"crore": "Cr", "lakh": "L", "million": "M", "billion": "B", "thousand": "K"}
RELATIVE_TOLERANCE = 0.005  # 0.5 %


def _printed_map(doc_id: str) -> dict[int, str]:
    out: dict[int, str] = {}
    with db.connect() as c:
        for r in c.execute("SELECT json FROM blocks WHERE doc_id=?", (doc_id,)):
            b = db.jload(r["json"], {})
            if b.get("type") in ("footer", "header"):
                label = M.printed_page(b.get("content", ""))
                if label:
                    out.setdefault(int((b.get("metadata", {}).get("preview") or {}).get("page") or b.get("page", 1)), label)
    return out


def fmt_value(value: float, scale: float | None, currency: str | None, unit: str | None, raw: str | None = None) -> str:
    """₹480.0 Cr from a base-unit value."""
    shown = value / (scale or 1.0)
    dec = len(raw.split(".")[1]) if raw and "." in raw else (1 if (scale or 1) > 1 else 0)
    sym = _CUR.get(currency or "", "")
    suffix = f" {_ABBR.get(unit or '', unit or '')}" if unit else ""
    return f"{sym}{shown:,.{dec}f}{suffix}"


_AUTHORITY = {"financial_statement": 0, "contract": 1, "debt_schedule": 1, "bank_statement": 1, "spreadsheet": 2, "presentation": 3, "cim": 3, "other": 4}


def doc_label(filename: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", filename).replace("_", " ")
    return stem


# ---------------------------------------------------------------------------
# contradictions
# ---------------------------------------------------------------------------


def _equal(a: dict, b: dict) -> bool:
    """Same value within 0.5 % or within rounding of the less precise printed figure."""
    va, vb = a["value"], b["value"]
    rounding = 0.0
    for f in (a, b):
        d = len(f["raw"].split(".")[1]) if f.get("raw") and "." in f["raw"] else 0
        rounding = max(rounding, 0.5 * 10 ** (-d) * (f["scale"] or 1.0))
    return abs(va - vb) <= max(RELATIVE_TOLERANCE * min(abs(va), abs(vb)), rounding)


def find_contradictions(workspace_id: str) -> list[dict[str, Any]]:
    facts = F.all_facts(workspace_id)
    groups: dict[tuple[str, str], list[dict]] = {}
    for f in facts:
        groups.setdefault((f["concept"], f["period"]), []).append(f)
    out: list[dict[str, Any]] = []
    pm_cache: dict[str, dict[int, str]] = {}
    for (concept, period), items in groups.items():
        by_doc: dict[str, dict] = {}
        for f in items:  # one representative fact per document: the first (tables beat sentences by order of insertion)
            by_doc.setdefault(f["doc_id"], f)
        if len(by_doc) < 2:
            continue
        reps = list(by_doc.values())
        comparable = [f for f in reps if f["currency"] == reps[0]["currency"]]
        if len(comparable) < 2:
            continue
        lo = min(comparable, key=lambda f: f["value"])
        hi = max(comparable, key=lambda f: f["value"])
        if _equal(lo, hi):
            continue
        gap = hi["value"] - lo["value"]
        gap_pct = gap / abs(lo["value"]) * 100 if lo["value"] else math.inf
        adjusted = any("adjust" in (f["label"] or "").lower() for f in (lo, hi))
        severity = "high" if gap_pct >= 5 else "medium" if gap_pct >= 1 else "low"
        if adjusted and severity != "low":
            severity = "medium" if severity == "high" else "low"
        sides = []
        for f in sorted(comparable, key=lambda f: -f["value"]):
            pm = pm_cache.setdefault(f["doc_id"], _printed_map(f["doc_id"]))
            sides.append({
                "doc_id": f["doc_id"], "filename": f["filename"], "label": f["label"], "page": f["page"],
                "printed_page": pm.get(f["page"]), "bbox": f["bbox"], "preview_pages": f["preview_pages"],
                "display": fmt_value(f["value"], f["scale"], f["currency"], f["unit"], f["raw"]), "value": f["value"],
                "confidence": f["confidence"], "agrees_with_lowest": _equal(f, lo),
            })
        # name the most authoritative document on each side (audited statements beat a spreadsheet or a CIM)
        types = {d["doc_id"]: d["doc_type"] for d in workspaces.documents(workspace_id)}
        rank = lambda sd: _AUTHORITY.get(types.get(sd["doc_id"], "other"), 4)
        hi_group = [x for x in sides if not x["agrees_with_lowest"]] or sides[:1]
        lo_group = [x for x in sides if x["agrees_with_lowest"]] or sides[-1:]
        hi_s, lo_s = min(hi_group, key=rank), min(lo_group, key=rank)
        name = F.CONCEPT_LABEL.get(concept, concept)
        note = "Definitions may differ (adjusted vs reported)." if adjusted else ""
        out.append({
            "id": hashlib.sha1(f"{concept}|{period}".encode()).hexdigest()[:12], "concept": concept, "concept_label": name,
            "period": period, "sides": sides, "primary": {"high": hi_s, "low": lo_s}, "gap_abs": gap, "gap_pct": round(gap_pct, 1), "severity": severity,
            "note": note,
            "summary": f"{name} {period}: {doc_label(hi_s['filename'])} says {hi_s['display']} (p.{hi_s['printed_page'] or hi_s['page']}) "
                       f"vs {doc_label(lo_s['filename'])} {lo_s['display']} (p.{lo_s['printed_page'] or lo_s['page']}) — {gap_pct:.1f}% gap",
        })
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda c: (order[c["severity"]], -c["gap_pct"]))
    return out


# ---------------------------------------------------------------------------
# totals that do not add up
# ---------------------------------------------------------------------------


def total_checks(workspace_id: str) -> list[dict[str, Any]]:
    """For every table with a 'Total' row: does the sum of the rows above it equal the stated total?"""
    issues: list[dict[str, Any]] = []
    with db.connect() as c:
        docs = {r["id"]: r["filename"] for r in c.execute("SELECT id, filename FROM documents WHERE workspace_id=?", (workspace_id,))}
        rows = c.execute("SELECT * FROM tables_store WHERE workspace_id=? AND kind='table'", (workspace_id,)).fetchall()
    for r in rows:
        grid = db.jload(r["grid_json"], {})
        data_rows: list[dict] = []
        for row in grid.get("rows", []):
            if row["is_total"] and re.match(r"^\s*(?:grand\s+)?total\b", row["label"], re.I):
                for ci in range(1, grid["n_cols"]):
                    vals = [x["cells"][ci] for x in data_rows if ci < len(x["cells"]) and x["cells"][ci]["v"] is not None and not x["cells"][ci].get("pct")]
                    tot = row["cells"][ci] if ci < len(row["cells"]) else None
                    if len(vals) < 3 or tot is None or tot["v"] is None or tot.get("pct"):
                        continue
                    decimals = max((len(str(v["raw"]).split(".")[1]) if "." in str(v["raw"] or "") else 0) for v in vals + [tot])
                    expected = sum(v["v"] for v in vals)
                    tol = max(0.5 * 10 ** (-decimals) * math.sqrt(len(vals)), 0.0005 * abs(tot["v"]))
                    if abs(expected - tot["v"]) > tol:
                        issues.append({
                            "doc_id": r["doc_id"], "filename": docs.get(r["doc_id"], ""), "table": r["title"] or "",
                            "column": grid["col_paths"][ci], "expected": round(expected, 6), "stated": tot["v"],
                            "diff": round(tot["v"] - expected, 6), "rows": len(vals), "page": int(tot["page"] or r["page"]),
                            "bbox": tot.get("bbox"), "unit": r["unit"], "currency": r["currency"], "scale": r["scale"],
                            "raw": tot["raw"],
                        })
                data_rows = []  # a new block of rows starts after a total
            elif not row["is_total"]:
                data_rows.append(row)
    return issues


# ---------------------------------------------------------------------------
# referenced but not provided
# ---------------------------------------------------------------------------

_REF = re.compile(r"\b(schedule|annexure|annex|exhibit|appendix)\s+([0-9]+|[A-Z])\b", re.I)


def missing_references(workspace_id: str) -> list[dict[str, Any]]:
    """'Schedule 3' (etc.) cited in a document but never present as a heading/section of the data room."""
    rows = list(index.get_index(workspace_id).rows.values())
    with db.connect() as c:
        docs = {r["id"]: r["filename"] for r in c.execute("SELECT id, filename FROM documents WHERE workspace_id=?", (workspace_id,))}
    provided: set[str] = set()
    cited: dict[str, dict] = {}
    for r in rows:
        head = " ".join(r["heading_path"]).lower()
        lead = r["text"].lower().lstrip()[:60]
        for m in _REF.finditer(head + " " + lead):
            if lead.startswith(m.group(0).lower()) or m.group(0).lower() in head:
                provided.add(f"{m.group(1).lower()} {m.group(2).lower()}")
        for m in _REF.finditer(r["text"]):
            label = f"{m.group(1).lower()} {m.group(2).lower()}"
            cited.setdefault(label, {"label": f"{m.group(1).title()} {m.group(2).upper()}", "doc_id": r["doc_id"],
                                     "filename": docs.get(r["doc_id"], ""), "page": r["pages"][0] if r["pages"] else None,
                                     "bbox": next((b["bbox"] for b in r["bboxes"] if b.get("bbox")), None),
                                     "context": r["text"][max(0, m.start() - 80): m.end() + 60].replace("\n", " ")})
    return [v for k, v in cited.items() if k not in provided]


# ---------------------------------------------------------------------------
# seller questions
# ---------------------------------------------------------------------------


def _evidence(doc_id: str, filename: str, page: int | None, bbox, label: str) -> dict[str, Any]:
    with db.connect() as c:
        r = c.execute("SELECT preview_pages FROM documents WHERE id=?", (doc_id,)).fetchone()
    return {"doc_id": doc_id, "filename": filename, "page": page, "bbox": bbox, "label": label,
            "preview_pages": r["preview_pages"] if r else 0}


def seller_questions(workspace_id: str) -> list[dict[str, Any]]:
    """A numbered, cited list of questions to put to the seller, with a reason and a severity."""
    items: list[dict[str, Any]] = []

    def add(kind: str, severity: str, question: str, why: str, evidence: list[dict[str, Any]], key: str) -> None:
        items.append({"id": hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:12], "kind": kind, "severity": severity,
                      "question": question, "why": why, "evidence": evidence})

    # 1. contradictions between documents
    for c in find_contradictions(workspace_id):
        a, b = c["primary"]["high"], c["primary"]["low"]
        add("contradiction", c["severity"],
            f"Please reconcile {c['concept_label']} for {c['period']}: {doc_label(a['filename'])} reports {a['display']} "
            f"(p.{a['printed_page'] or a['page']}) while {doc_label(b['filename'])} reports {b['display']} (p.{b['printed_page'] or b['page']}). "
            "Which figure is correct, and what explains the difference?",
            f"{c['gap_pct']}% gap between two documents. {c['note']}".strip(),
            [_evidence(s["doc_id"], s["filename"], s["page"], s["bbox"], f"{s['label']} {s['display']}") for s in c["sides"]], c["id"])

    # 2. totals that do not add up
    for t in total_checks(workspace_id):
        add("total_mismatch", "high",
            f"In {doc_label(t['filename'])}, the stated total of '{t['column']}' is {fmt_value(t['stated'], 1.0, t['currency'], None, t['raw'])} "
            f"but the {t['rows']} rows above it add up to {fmt_value(t['expected'], 1.0, t['currency'], None, t['raw'])}. Please explain the difference of "
            f"{fmt_value(abs(t['diff']), 1.0, t['currency'], None, t['raw'])}.",
            "Row-sum versus stated total do not agree.",
            [_evidence(t["doc_id"], t["filename"], t["page"], t["bbox"], "stated total")], f"{t['doc_id']}|{t['column']}|{t['page']}")

    # 3. references to documents that were not provided
    for m in missing_references(workspace_id):
        add("missing_reference", "medium",
            f"{m['label']} is referenced in {doc_label(m['filename'])} (p.{m['page']}) but is not in the data room. Please provide it.",
            f"Referenced as: “…{m['context'].strip()}…”", [_evidence(m["doc_id"], m["filename"], m["page"], m["bbox"], m["label"])], m["label"])

    # 4. chunk-level flags: hardcoded values, uncached formulas, scans, estimated chart values
    with db.connect() as c:
        docs = {r["id"]: r["filename"] for r in c.execute("SELECT id, filename FROM documents WHERE workspace_id=?", (workspace_id,))}
        chunks = c.execute("SELECT doc_id, kind, text, pages_json, bboxes_json, flags_json, min_confidence FROM chunks "
                           "WHERE workspace_id=? AND trust='normal' AND flags_json != '[]'", (workspace_id,)).fetchall()
    seen: set[tuple] = set()
    for ch in chunks:
        flags = set(db.jload(ch["flags_json"], []))
        pages = db.jload(ch["pages_json"], [])
        box = next((b for b in db.jload(ch["bboxes_json"], []) if b.get("bbox")), {})
        page = pages[0] if pages else None
        fn = docs.get(ch["doc_id"], "")
        ev = [_evidence(ch["doc_id"], fn, page, box.get("bbox"), "flagged value")]
        if "manual_override_suspected" in flags and (ch["doc_id"], "hc") not in seen:
            seen.add((ch["doc_id"], "hc"))
            add("hardcoded_value", "medium",
                f"In {doc_label(fn)}, a headline financial line (e.g. EBITDA) is a typed number, not derived from the formulas in the workbook. "
                "Please provide the calculation and supporting schedule behind it.",
                "A hardcoded value where a formula-driven model would have a formula is a classic sign of a manual override.", ev, f"hc|{ch['doc_id']}")
        if "formulas_without_cached_values" in flags and (ch["doc_id"], "fc") not in seen:
            seen.add((ch["doc_id"], "fc"))
            add("uncached_formulas", "low", f"{doc_label(fn)} contains formulas without calculated values. Please send a recalculated copy.",
                "The numbers cannot be read without recalculation.", ev, f"fc|{ch['doc_id']}")
        if ({"low_ocr_confidence", "needs_review"} & flags) and ch["min_confidence"] is not None and (ch["doc_id"], page, "ocr") not in seen:
            seen.add((ch["doc_id"], page, "ocr"))
            add("low_confidence_scan", "medium",
                f"Page {page} of {doc_label(fn)} is a low-quality scan (extraction confidence {ch['min_confidence']:.0%}). "
                "Please provide a clean digital copy so the figures can be verified.", "Numbers read from this page may be wrong.", ev, f"ocr|{ch['doc_id']}|{page}")
        if "values_estimated" in flags and (ch["doc_id"], page, "est") not in seen:
            seen.add((ch["doc_id"], page, "est"))
            add("estimated_chart", "low",
                f"The chart on page {page} of {doc_label(fn)} has no data labels, so its values were estimated from the image. "
                "Please provide the underlying data.", "Chart values are measured, not transcribed.", ev, f"est|{ch['doc_id']}|{page}")

    # 5. quarantined / hidden content (one item per document + page + kind of finding)
    with db.connect() as c:
        quar = c.execute("SELECT * FROM quarantine WHERE workspace_id=? ORDER BY created_at", (workspace_id,)).fetchall()
    from rag import security

    groups: dict[tuple, list] = {}
    for q in quar:
        if q["reason"] == "hidden_sheet":
            g = "hidden_sheet"
        elif q["reason"] == "hidden_row_col":
            g = "hidden_rowcol"
        elif q["reason"].startswith("hidden_") or q["kind"] != "finding" and q["reason"] in security.REASON_LABEL and q["reason"] not in (
                "instruction_override", "role_hijack", "prompt_exfiltration", "system_prompt_mention", "answer_steering", "chat_template_tokens"):
            g = "hidden_text"
        elif q["kind"] == "finding":
            g = "finding"
        else:
            g = "injection"
        groups.setdefault((q["doc_id"], q["page"], g if g != "finding" else q["reason"]), []).append(q)
    done_pages: set[tuple] = set()
    for (doc_id, page, g), qs in groups.items():
        fn = docs.get(doc_id, "")
        q0 = qs[0]
        label = security.REASON_LABEL.get(q0["reason"], q0["reason"])
        ev = [_evidence(doc_id, fn, page, db.jload(q0["bbox_json"]), label)]
        has_both = (doc_id, page, "injection") in groups and (doc_id, page, "hidden_text") in groups
        if g in ("hidden_text", "injection") and has_both:
            if (doc_id, page) in done_pages:
                continue
            done_pages.add((doc_id, page))
            add("hidden_instruction", "high",
                f"{doc_label(fn)} p.{page} contains hidden text that instructs an AI system to give a specific answer. "
                "Please confirm who added it, when, and why.", f"“{q0['snippet'][:160]}”", ev, f"hi|{doc_id}|{page}")
        elif g == "hidden_text":
            add("hidden_content", "high", f"{doc_label(fn)}{' p.' + str(page) if page else ''} contains text that is invisible to a reader. "
                "Please confirm who added it and why.", f"{label}: “{q0['snippet'][:140]}”", ev, f"q|{q0['id']}")
        elif g == "injection":
            add("injection", "high", f"{doc_label(fn)}{' p.' + str(page) if page else ''} contains text addressed to an AI system. "
                "Please confirm the provenance of this document.", f"{label}: “{q0['snippet'][:140]}”", ev, f"q|{q0['id']}")
        elif g == "hidden_sheet":
            add("hidden_content", "medium", f"{doc_label(fn)} contains a hidden sheet. Please explain what it contains and why it is hidden.",
                f"Hidden content: {q0['snippet'][:200]}", ev, f"q|{q0['id']}")
        elif g == "hidden_rowcol":
            add("hidden_content", "medium", f"{doc_label(fn)} has hidden rows or columns. Please provide the unhidden version.", q0["snippet"][:160], ev, f"q|{q0['id']}")
        else:
            add("active_content", "medium", f"{doc_label(fn)} contains active content ({label}). Please provide a clean copy.", q0["snippet"][:160], ev, f"q|{q0['id']}")

    # 6. diligence-pack gaps: a topic that NO document in the pack answers
    from rag import packs

    for gap in packs.latest_gaps(workspace_id):
        add("pack_gap", "low", f"{gap['pack']}: none of the documents addresses “{gap['question']}”. Is it covered somewhere else, or is it missing?",
            "Not found in any document of the pack.", [], f"gap|{gap['pack']}|{gap['question']}")

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: order[i["severity"]])
    for n, it in enumerate(items, 1):
        it["n"] = n
    return items
