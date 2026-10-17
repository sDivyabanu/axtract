"""Offline setup and verification of the Pix2Text-MFR assets used by scanned-math recognition.

This tool NEVER downloads anything. Model files must already exist on the machine (copied from a trusted local source or
fetched by you through your normal, approved process); it verifies them against `scanned_math_assets.manifest.json`
(size + SHA-256), installs them into the default directory, and proves the recognizer can load them.

    python scripts/scanned_math_assets.py verify  [--dir DIR]
    python scripts/scanned_math_assets.py install --from SOURCE_DIR [--dest DIR] [--force]
    python scripts/scanned_math_assets.py check   [--dir DIR]

`--dir/--dest` default to AXTRACT_MFR_DIR, then <AXTRACT_MODEL_DIR or backend/model_weights>/pix2text_mfr.
Exit status 0 means success; any mismatch, missing file or load failure is non-zero with a one-line reason per problem.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((Path(__file__).with_name("scanned_math_assets.manifest.json")).read_text(encoding="utf8"))


def default_dir() -> Path:
    if os.environ.get("AXTRACT_MFR_DIR"):
        return Path(os.environ["AXTRACT_MFR_DIR"])
    return Path(os.environ.get("AXTRACT_MODEL_DIR", ROOT / "backend" / "model_weights")) / "pix2text_mfr"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def problems(folder: Path) -> list[str]:
    out = []
    for name, want in MANIFEST["files"].items():
        p = folder / name
        if not p.is_file():
            out.append(f"missing: {name}")
        elif p.stat().st_size != want["bytes"]:
            out.append(f"wrong size: {name} ({p.stat().st_size} != {want['bytes']})")
        elif sha256(p) != want["sha256"]:
            out.append(f"wrong SHA-256: {name}")
    return out


def cmd_verify(args) -> int:
    folder = Path(args.dir) if args.dir else default_dir()
    bad = problems(folder)
    for line in bad:
        print(f"FAIL {line}")
    print(f"{'OK' if not bad else 'NOT OK'}: {folder} ({len(MANIFEST['files'])} files checked against the manifest)")
    return 1 if bad else 0


def cmd_install(args) -> int:
    src, dest = Path(args.source), Path(args.dest) if args.dest else default_dir()
    bad = problems(src)
    if bad:
        print("Refusing to install: the source does not match the manifest:\n  " + "\n  ".join(bad))
        return 1
    if dest.resolve() == src.resolve():
        print(f"Source and destination are the same folder; nothing to do ({dest}).")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for name in MANIFEST["files"]:
        target = dest / name
        if target.exists() and sha256(target) == MANIFEST["files"][name]["sha256"]:
            continue
        if target.exists() and not args.force:
            print(f"Refusing to overwrite a different existing file: {target} (use --force)")
            return 1
        tmp = target.with_name(f".{name}.tmp")
        shutil.copy2(src / name, tmp)
        os.replace(tmp, target)
        print(f"installed {name}")
    return cmd_verify(argparse.Namespace(dir=str(dest)))


def cmd_check(args) -> int:
    folder = Path(args.dir) if args.dir else default_dir()
    if cmd_verify(argparse.Namespace(dir=str(folder))):
        return 1
    os.environ["AXTRACT_MFR_DIR"] = str(folder)
    sys.path.insert(0, str(ROOT / "backend"))
    import importlib.metadata as md

    versions = {}
    for pkg in ("onnxruntime", "tokenizers", "numpy", "Pillow"):
        try:
            versions[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            print(f"FAIL required package not installed: {pkg}")
            return 1
    drift = {p: (v, MANIFEST["runtime"][p]) for p, v in versions.items() if v != MANIFEST["runtime"][p]}
    from PIL import Image, ImageDraw

    from services.vision import pix2text_mfr

    status = pix2text_mfr.mfr_status()
    if not status["available"]:
        print(f"FAIL recognizer reports unavailable: {status}")
        return 1
    image = Image.new("RGB", (260, 64), "white")
    ImageDraw.Draw(image).text((12, 20), "x^2 + y^2 = 9", fill="black")
    started = time.perf_counter()
    prediction = pix2text_mfr.recognize(image, max_seconds=30)
    seconds = time.perf_counter() - started
    print(json.dumps({"versions": versions, "pinned_versions_differ": drift, "first_call_seconds": round(seconds, 2),
                      "completed": prediction.completed, "stop_reason": prediction.stop_reason, "tokens": prediction.tokens,
                      "latex": prediction.latex[:80]}, indent=1))
    if not prediction.completed or not prediction.latex.strip():
        print("FAIL the recognizer loaded but did not produce a LaTeX string")
        return 1
    print("OK: Pix2Text-MFR loads from", folder)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify"); v.add_argument("--dir"); v.set_defaults(fn=cmd_verify)
    i = sub.add_parser("install"); i.add_argument("--from", dest="source", required=True); i.add_argument("--dest")
    i.add_argument("--force", action="store_true"); i.set_defaults(fn=cmd_install)
    c = sub.add_parser("check"); c.add_argument("--dir"); c.set_defaults(fn=cmd_check)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
