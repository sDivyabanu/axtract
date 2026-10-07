#!/usr/bin/env python3
"""Run every file in sample_files/ through the real parse + preview pipeline.

Writes reports/<tag>_report.md and reports/<tag>_report.json (default tag: baseline).

Usage (from the repo root, with the backend virtualenv):
    backend/.venv/bin/python scripts/run_samples.py [--tag baseline] [--samples sample_files]

Parsing goes through the real FastAPI app (POST /api/parse) so serialization and
error handling are exercised. If the endpoint answers with a 5xx, parse_upload()
is re-run directly to capture the full traceback. The preview column calls the
real POST /api/preview endpoint.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.datastructures import UploadFile  # noqa: E402

from main import app  # noqa: E402
from services.parse_service import parse_upload  # noqa: E402

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "txt": "text/plain",
}

PREVIEW_OUT = ROOT / "reports" / "previews"


def sniff_format(data: bytes) -> str:
    """Detect the real container from magic bytes (independent of the extension)."""
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"PK\x03\x04"):
        import io
        import zipfile

        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return "zip(corrupt)"
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        return "zip"
    return "unknown"


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def chart_has_numeric_values(block: dict) -> bool:
    """True only for *real* extracted values (placeholder output does not count)."""
    data = (block.get("metadata") or {}).get("chart_data") or {}
    if not data or data.get("extraction_method") == "placeholder":
        return False
    values = data.get("values")
    if isinstance(values, list) and values and all(_is_number(v) for v in values):
        return True
    for series in data.get("series") or []:
        vals = series.get("values") if isinstance(series, dict) else None
        if isinstance(vals, list) and vals and all(_is_number(v) for v in vals):
            return True
    return False


def equation_has_latex(block: dict) -> bool:
    """True only when LaTeX came from a real method (not 'none' / placeholder)."""
    meta = block.get("metadata") or {}
    method = str(meta.get("latex_method", "none"))
    if "placeholder" in method or method in ("none", "fallback", ""):
        return False
    return bool(meta.get("latex"))


def try_preview(client: TestClient, path: Path, ext: str) -> tuple[bool, str]:
    """Call the real preview endpoint (page 1) and save the image on success."""
    resp = client.post(
        "/api/preview?page=0",
        files={"file": (path.name, path.read_bytes(), MIME.get(ext, "application/octet-stream"))},
    )
    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
        PREVIEW_OUT.mkdir(parents=True, exist_ok=True)
        (PREVIEW_OUT / f"{path.stem}_p1.png").write_bytes(resp.content)
        return True, ""
    return False, f"HTTP {resp.status_code}: {resp.text[:300]}"


def run_file(client: TestClient, path: Path) -> dict:
    ext = path.suffix.lower().lstrip(".")
    data = path.read_bytes()
    row: dict = {
        "file": path.name,
        "detected_format": sniff_format(data),
        "status": None,
        "error_code": None,
        "pages": None,
        "block_counts": {},
        "charts_detected": 0,
        "charts_with_values": 0,
        "equations_detected": 0,
        "equations_with_latex": 0,
        "preview_ok": False,
        "preview_error": "",
        "traceback": "",
        "time_s": 0.0,
    }

    started = time.perf_counter()
    resp = client.post(
        "/api/parse",
        files={"file": (path.name, data, MIME.get(ext, "application/octet-stream"))},
    )
    row["time_s"] = round(time.perf_counter() - started, 2)

    try:
        body = resp.json()
    except ValueError:
        body = {}

    if resp.status_code == 200:
        row["status"] = body.get("status")
        row["pages"] = body.get("page_count")
        blocks = body.get("blocks", [])
        row["block_counts"] = dict(Counter(b["type"] for b in blocks))
        charts = [b for b in blocks if b["type"] == "chart"]
        eqs = [b for b in blocks if b["type"] == "equation"]
        row["charts_detected"] = len(charts)
        row["charts_with_values"] = sum(chart_has_numeric_values(b) for b in charts)
        row["equations_detected"] = len(eqs)
        row["equations_with_latex"] = sum(equation_has_latex(b) for b in eqs)
        if body.get("errors"):
            row["traceback"] = "; ".join(
                f"[{e.get('code')}] p{e.get('page')}: {e.get('message')}" for e in body["errors"]
            )
    else:
        row["status"] = f"HTTP {resp.status_code}"
        row["error_code"] = (body.get("error") or {}).get("code") or f"HTTP_{resp.status_code}"
        if resp.status_code >= 500:
            # Re-run directly to capture the real traceback.
            try:
                with path.open("rb") as fh:
                    parse_upload(UploadFile(file=fh, filename=path.name))
            except Exception:
                row["traceback"] = traceback.format_exc()

    row["preview_ok"], row["preview_error"] = try_preview(client, path, ext)
    return row


def fmt_counts(counts: dict) -> str:
    return ", ".join(f"{k}:{v}" for k, v in sorted(counts.items())) or "-"


def write_reports(rows: list[dict], tag: str) -> None:
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"{tag}_report.json").write_text(json.dumps(rows, indent=2))

    lines = [
        f"# {tag.capitalize()} report",
        "",
        "| file | format | status / error | pages | blocks by type | charts | charts w/ values | equations | equations w/ LaTeX | preview OK | time (s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        status = r["error_code"] or r["status"]
        lines.append(
            f"| {r['file']} | {r['detected_format']} | {status} | {r['pages'] if r['pages'] is not None else '-'} "
            f"| {fmt_counts(r['block_counts'])} | {r['charts_detected']} | {r['charts_with_values']} "
            f"| {r['equations_detected']} | {r['equations_with_latex']} "
            f"| {'yes' if r['preview_ok'] else 'NO'} | {r['time_s']} |"
        )

    problems = [r for r in rows if r["traceback"] or (not r["preview_ok"])]
    if problems:
        lines += ["", "## Errors, warnings and preview failures", ""]
        for r in problems:
            lines.append(f"### {r['file']}")
            if r["traceback"]:
                lines += ["", "```", r["traceback"].strip(), "```"]
            if not r["preview_ok"]:
                lines += ["", f"Preview: `{r['preview_error']}`"]
            lines.append("")

    slowest = max(rows, key=lambda r: r["time_s"])
    lines += ["", f"Slowest file: {slowest['file']} ({slowest['time_s']} s)"]
    (out / f"{tag}_report.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="baseline")
    parser.add_argument("--samples", default=str(ROOT / "sample_files"))
    args = parser.parse_args()

    client = TestClient(app, raise_server_exceptions=False)
    files = sorted(p for p in Path(args.samples).iterdir() if p.is_file() and not p.name.startswith("."))
    # Only the actual samples, not the docs that live next to them.
    files = [p for p in files if p.suffix.lower() not in (".md", ".json")]

    rows = []
    for p in files:
        print(f"-> {p.name}", flush=True)
        rows.append(run_file(client, p))

    write_reports(rows, args.tag)
    print(f"\nWrote reports/{args.tag}_report.md and reports/{args.tag}_report.json")


if __name__ == "__main__":
    main()
