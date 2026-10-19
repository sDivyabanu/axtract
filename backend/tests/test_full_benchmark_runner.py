"""Full-benchmark runner: assertion discovery, empty-page placeholders (the official scorer zeroes a candidate with a missing page)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import full_benchmark as fb  # noqa: E402


def dataset(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    rows = [{"id": "a1", "pdf": "tables/t_pg1.pdf", "page": 1, "type": "table"},
            {"id": "a2", "pdf": "tables/t_pg1.pdf", "page": 1, "type": "table"},
            {"id": "b1", "pdf": "old_scans/s.pdf", "page": 1, "type": "present"}]
    (data / "x.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf8")
    return data


def test_every_pdf_with_assertions_is_listed_once_and_can_be_filtered(tmp_path):
    data = dataset(tmp_path)
    assert fb.assertion_pdfs(data, None) == ["old_scans/s.pdf", "tables/t_pg1.pdf"]
    assert fb.assertion_pdfs(data, "tables") == ["tables/t_pg1.pdf"]


def test_missing_pages_follow_the_scorers_file_naming(tmp_path):
    data, cand = dataset(tmp_path), tmp_path / "cand"
    (cand / "tables").mkdir(parents=True)
    (cand / "tables" / "t_pg1_pg1_repeat1.md").write_text("x", encoding="utf8")
    assert fb.missing_pages(data, cand) == [("old_scans/s.pdf", 1)]


def test_missing_pages_are_recorded_as_empty_files_and_real_output_is_kept(tmp_path):
    data, cand = dataset(tmp_path), tmp_path / "cand"
    (cand / "tables").mkdir(parents=True)
    real = cand / "tables" / "t_pg1_pg1_repeat1.md"
    real.write_text("kept", encoding="utf8")
    written = fb.fill_missing(data, cand)
    assert written == ["old_scans/s.pdf page 1"]
    assert (cand / "old_scans" / "s_pg1_repeat1.md").read_text(encoding="utf8") == ""
    assert real.read_text(encoding="utf8") == "kept"
    assert fb.missing_pages(data, cand) == []


def test_shards_are_disjoint_and_cover_the_list():
    pdfs = [f"arxiv_math/{i:03}.pdf" for i in range(522)]
    for n in (1, 2, 3, 7):
        parts = [fb.shard(pdfs, f"{k}/{n}") for k in range(n)]
        assert sorted(p for part in parts for p in part) == pdfs
        assert sum(len(part) for part in parts) == len(pdfs)
    assert fb.shard(pdfs, None) == pdfs


def test_cloud_usage_reads_cost_and_tokens_from_block_metadata():
    blocks = [{"metadata": {"reserved_or_reported_usd": 0.0125, "requests": [{}, {}],
                            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}},
              {"metadata": {"cloud_page_block": True}}]
    assert fb.cloud_usage(blocks) == {"azure_usd": 0.0125, "azure_requests": 2,
                                      "azure_usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}
    assert fb.cloud_usage(None)["azure_usd"] is None
