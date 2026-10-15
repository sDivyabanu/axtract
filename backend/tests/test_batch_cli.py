from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import axtract_batch


def test_batch_writes_one_markdown_file_per_pdf_and_keeps_subfolders(tmp_path, monkeypatch):
    source = tmp_path / "pdfs"
    (source / "a").mkdir(parents=True)
    (source / "b").mkdir()
    (source / "a" / "same.pdf").write_bytes(b"pdf")
    (source / "b" / "same.pdf").write_bytes(b"pdf")

    def fake_parse(path, _filename, *, persistent_preview):
        assert persistent_preview is False
        return SimpleNamespace(markdown=f"content from {path.parent.name}")

    monkeypatch.setattr(axtract_batch, "parse_path", fake_parse)
    output = tmp_path / "markdown"
    result = axtract_batch.run_batch(source, output)

    assert result["total"] == result["succeeded"] == 2
    assert result["failed"] == []
    assert (output / "a" / "same.md").read_text(encoding="utf-8") == "content from a"
    assert (output / "b" / "same.md").read_text(encoding="utf-8") == "content from b"


def test_batch_continues_after_a_pdf_failure(tmp_path, monkeypatch):
    source = tmp_path / "pdfs"
    source.mkdir()
    (source / "ok.pdf").write_bytes(b"pdf")
    (source / "bad.pdf").write_bytes(b"pdf")

    def fake_parse(path, _filename, **_kwargs):
        if path.name == "bad.pdf":
            raise RuntimeError("cannot parse")
        return SimpleNamespace(markdown="valid output")

    monkeypatch.setattr(axtract_batch, "parse_path", fake_parse)
    output = tmp_path / "markdown"
    result = axtract_batch.run_batch(source, output, workers=2)

    assert result["total"] == 2
    assert result["succeeded"] == 1
    assert len(result["failed"]) == 1
    assert (output / "ok.md").read_text(encoding="utf-8") == "valid output"
    assert not (output / "bad.md").exists()
