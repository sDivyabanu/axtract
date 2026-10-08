"""CLI entry point (DQCL §5): parse <file> --out <dir>.

Runs the exact same pipeline as the HTTP endpoint and writes:
  <out>/<stem>.md          — body markdown in natural reading order
  <out>/<stem>.json        — every block with type, page, bbox, confidence

Examples:
    python backend/cli.py report.pdf --out result/
    python backend/cli.py invoice.png statement.docx --out result/
"""

from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path

# Allow running as `python backend/cli.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")

from starlette.datastructures import UploadFile as BytesUpload  # noqa: E402

from services.parse_service import parse_upload  # noqa: E402
from utils import deadline  # noqa: E402


def parse_file(path: Path) -> dict:
    data = path.read_bytes()
    upload = BytesUpload(file=BytesIO(data), filename=path.name)
    deadline.start()
    response = parse_upload(upload)
    return response.model_dump()


def write_output(result: dict, out_dir: Path) -> tuple[Path, Path]:
    filename = Path(result.get("filename") or "document").name
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{filename}.md"
    json_path = out_dir / f"{filename}.json"
    md_path.write_text(result.get("markdown") or "", encoding="utf-8")
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return md_path, json_path


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="parse", description="Parse any supported file into Markdown + JSON blocks."
    )
    parser.add_argument("files", nargs="+", type=Path, help="files to parse")
    parser.add_argument("--out", type=Path, default=Path("out"), help="output directory")
    args = parser.parse_args()

    failed = 0
    for path in args.files:
        if not path.exists():
            print(f"error: {path}: no such file", file=sys.stderr)
            failed += 1
            continue
        try:
            result = parse_file(path)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", "PROCESSING_FAILED")
            print(f"error: {path}: [{code}] {exc}", file=sys.stderr)
            failed += 1
            continue
        md_path, json_path = write_output(result, args.out)
        status = result.get("status", "?")
        print(
            f"{path.name}: {status} — {result.get('page_count', '?')} pages, "
            f"{len(result.get('blocks', []))} blocks -> {md_path}, {json_path}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
