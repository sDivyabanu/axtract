"""Reproducible scanned-math setup: pinned packages, the asset manifest and the offline asset tool."""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

spec = importlib.util.spec_from_file_location("scanned_math_assets", ROOT / "scripts" / "scanned_math_assets.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

REQUIREMENTS = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf8")


def pinned(name):
    m = re.search(rf"^{re.escape(name)}==([^\s#]+)", REQUIREMENTS, re.M | re.I)
    return m.group(1) if m else None


@pytest.mark.parametrize("package", ["onnxruntime", "tokenizers", "numpy", "Pillow", "latex2mathml", "rapidocr-onnxruntime", "rapid-layout"])
def test_recognizer_packages_are_declared_and_pinned(package):
    assert pinned(package), f"{package} must be pinned with == in backend/requirements.txt"


def test_manifest_runtime_versions_match_the_requirement_pins():
    for package, version in tool.MANIFEST["runtime"].items():
        assert pinned(package) == version


def test_python_313_incompatible_legacy_packages_are_not_in_the_main_requirements():
    assert not re.search(r"^rapid-latex-ocr", REQUIREMENTS, re.M)
    assert "rapid-latex-ocr" in (ROOT / "backend" / "requirements-optional.txt").read_text(encoding="utf8")


def test_manifest_covers_every_file_the_recognizer_loads():
    from services.vision import pix2text_mfr
    assert set(pix2text_mfr._FILES) <= set(tool.MANIFEST["files"])
    assert all(len(v["sha256"]) == 64 and v["bytes"] > 0 for v in tool.MANIFEST["files"].values())
    assert tool.MANIFEST["license"].startswith("MIT") and "breezedeus/pix2text-mfr" in tool.MANIFEST["source_repository"]


def fake_assets(folder: Path, monkeypatch, content=b"x"):
    """Small stand-in files plus a manifest that describes them (the real 118 MB files are not needed to test the tool)."""
    import hashlib
    folder.mkdir(parents=True, exist_ok=True)
    files = {}
    for name in ("a.onnx", "b.json"):
        (folder / name).write_bytes(content + name.encode())
        files[name] = {"bytes": len(content + name.encode()), "sha256": hashlib.sha256(content + name.encode()).hexdigest()}
    monkeypatch.setattr(tool, "MANIFEST", {**tool.MANIFEST, "files": files})


def test_verify_accepts_matching_files_and_rejects_missing_wrong_size_and_tampered(tmp_path, monkeypatch):
    src = tmp_path / "src"
    fake_assets(src, monkeypatch)
    assert tool.problems(src) == []
    (src / "b.json").write_bytes(b"x" + b"b.jsoN")                       # same size, different bytes
    assert tool.problems(src) == ["wrong SHA-256: b.json"]
    (src / "a.onnx").write_bytes(b"short")
    assert any(p.startswith("wrong size: a.onnx") for p in tool.problems(src))
    (src / "a.onnx").unlink()
    assert "missing: a.onnx" in tool.problems(src)


def test_install_copies_verified_files_and_refuses_bad_sources_or_overwrites(tmp_path, monkeypatch, capsys):
    src, dest = tmp_path / "src", tmp_path / "dest"
    fake_assets(src, monkeypatch)
    assert tool.cmd_install(SimpleNamespace(source=str(src), dest=str(dest), force=False)) == 0
    assert tool.problems(dest) == []
    # a different file already in place is never silently replaced
    (dest / "a.onnx").write_bytes(b"different")
    assert tool.cmd_install(SimpleNamespace(source=str(src), dest=str(dest), force=False)) == 1
    assert (dest / "a.onnx").read_bytes() == b"different"
    assert tool.cmd_install(SimpleNamespace(source=str(src), dest=str(dest), force=True)) == 0
    # a source that does not match the manifest is refused before anything is copied
    (src / "b.json").write_bytes(b"corrupt")
    other = tmp_path / "other"
    assert tool.cmd_install(SimpleNamespace(source=str(src), dest=str(other), force=False)) == 1
    assert not other.exists()


def test_the_tool_never_touches_the_network():
    source = (ROOT / "scripts" / "scanned_math_assets.py").read_text(encoding="utf8")
    assert not re.search(r"urllib|requests|http\.client|socket|urlretrieve|urlopen", source)


def test_scanned_math_on_without_assets_warns_once_and_never_crashes(monkeypatch, caplog):
    from models.document import BlockType, DocumentBlock
    from extractors.base import ExtractionResult
    from services import scanned_math
    from services.vision import pix2text_mfr

    monkeypatch.setenv("AXTRACT_SCANNED_MATH", "1")
    monkeypatch.setattr(scanned_math, "_warned_unavailable", False)
    monkeypatch.setattr(scanned_math, "hidden_scan_words", lambda *a: {})
    monkeypatch.setattr(pix2text_mfr, "mfr_status", lambda: {"available": False, "weights_missing": ["encoder_model.onnx"], "tokenizers_available": False})
    block = DocumentBlock(id="o", type=BlockType.PARAGRAPH, content="x", page=1, bbox=(0, 0, .5, .1), confidence=.9, extractor="rapidocr",
                          metadata={"route": "scanned"})
    with caplog.at_level(logging.WARNING, logger=scanned_math.logger.name):
        for _ in range(2):
            result = ExtractionResult(page_count=1, blocks=[DocumentBlock(**{**block.__dict__})])
            scanned_math.route_scanned_math(result, Path("unused.pdf"))
    warnings = [r for r in caplog.records if "Pix2Text-MFR is unavailable" in r.getMessage()]
    assert len(warnings) == 1 and "scanned_math_assets.py" in warnings[0].getMessage()
    assert result.blocks[0].metadata["scanned_math_page"]["stop_reason"] == "model_unavailable"
