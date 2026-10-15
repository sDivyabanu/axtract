"""Synthetic fixtures: a tiny fake olmOCR dataset plus mocked extraction and scoring.

No real PDF is ever opened and the official scorer is never launched.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from benchmark import config  # noqa: E402
from benchmark.runner import BenchmarkRunner  # noqa: E402

# category -> (jsonl name, number of pdfs). Note: "tables" is fed by table_tests.jsonl like the real data.
LAYOUT = {"alpha": ("alpha.jsonl", 8), "beta": ("beta.jsonl", 6), "tables": ("table_tests.jsonl", 3)}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def dataset(tmp_path: Path) -> Path:
    data = tmp_path / "official" / "bench_data"
    for cat, (jsonl, n) in LAYOUT.items():
        (data / "pdfs" / cat).mkdir(parents=True)
        rows = []
        for i in range(n):
            name = f"{cat}/doc{i:02d}.pdf"
            (data / "pdfs" / name).write_bytes(b"%PDF-1.4 synthetic")
            for t in range(2):
                rows.append({"pdf": name, "page": 1, "id": f"{cat}_doc{i:02d}_t{t}", "type": "present", "text": "x"})
        (data / jsonl).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    # an orphan test row whose PDF does not exist must be ignored
    with (data / "alpha.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pdf": "alpha/missing.pdf", "page": 1, "id": "orphan", "type": "present"}) + "\n")
    return data


@pytest.fixture
def settings(tmp_path: Path, dataset: Path) -> config.BenchmarkSettings:
    return config.BenchmarkSettings(
        data_dir=dataset, workspace=tmp_path / "workspace", scorer_python=Path("unused"), repo_root=tmp_path / "repo",
    )


class FakeExtract:
    """Writes one markdown file per page like the real adapter; documents named *doc03* fail."""

    def __init__(self, fail_substr: str | None = "doc03", gate: threading.Event | None = None):
        self.fail_substr, self.gate, self.calls = fail_substr, gate, []
        self._lock = threading.Lock()

    def __call__(self, pdf: Path, rel: Path, candidate_dir: Path) -> dict:
        with self._lock:
            self.calls.append(str(rel).replace("\\", "/"))
        if self.gate:
            self.gate.wait(5)
        if self.fail_substr and self.fail_substr in rel.name:
            return {"success": False, "pages": 0, "error": "RuntimeError: boom", "total_elapsed_ms": 5.0}
        out = candidate_dir / rel.parent
        out.mkdir(parents=True, exist_ok=True)
        for pg in (1, 2):
            (out / f"{rel.stem}_pg{pg}_repeat1.md").write_text("md", encoding="utf-8")
        return {"success": True, "pages": 2, "total_elapsed_ms": 200.0}


SCORER_STDOUT = """
Candidate: axtract
  Average Score: 62.5% (95% CI: [50.0%, 70.0%]) over 40 tests.

============================================================
Final Summary with 95% Confidence Intervals:
axtract              : Average Score: 62.5% ± 4.5% (average of per-JSONL scores)
    present : 62.5% average pass rate over 40 tests

    Results by JSONL file:
        alpha.jsonl                   : 50.0% (5/10 tests)
        baseline                      : 100.0% (3/3 tests)
        beta.jsonl                    : 75.0% (3/4 tests)
        table_tests.jsonl             : 60.0% (3/5 tests)
"""


class FakeScore:
    def __init__(self, returncode: int = 0):
        self.returncode, self.calls = returncode, 0

    def __call__(self, scoring_dir: Path, report_html: Path, failed_jsonl: Path) -> dict:
        self.calls += 1
        report_html.write_text("<html>report</html>", encoding="utf-8")
        failed_jsonl.write_text(json.dumps({"id": "alpha_doc00_t0", "pdf": "alpha/doc00.pdf", "page": 1, "type": "present"}) + "\n", encoding="utf-8")
        return {"returncode": self.returncode, "stdout": SCORER_STDOUT, "stderr": ""}


def fake_git(_: Path) -> dict:
    return {"sha": "a" * 40, "branch": "feature/test", "dirty": False}


@pytest.fixture
def make_runner(settings):
    def factory(extract=None, score=None, git=fake_git):
        return BenchmarkRunner(settings, extract_fn=extract or FakeExtract(), score_fn=score or FakeScore(), git_fn=git)

    return factory
