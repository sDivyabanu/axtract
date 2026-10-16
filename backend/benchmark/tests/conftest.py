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
    data = tmp_path / "official" / "benchmarks" / "olmOCR-bench" / "bench_data"
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
        data_dir=dataset, workspace=tmp_path / "workspace", scorer_python=Path("unused"),
        extract_python=Path(sys.executable), repo_root=tmp_path / "repo",
    )


class FakeExtract:
    """Writes one markdown file per page like the real adapter; documents named *doc03* fail."""

    def __init__(self, fail_substr: str | None = "doc03", gate: threading.Event | None = None, empty_substr: str | None = None):
        self.fail_substr, self.gate, self.empty_substr, self.calls = fail_substr, gate, empty_substr, []
        self._lock = threading.Lock()

    def __call__(self, pdf: Path, rel: Path, candidate_dir: Path) -> dict:
        with self._lock:
            self.calls.append(str(rel).replace("\\", "/"))
        if self.gate:
            self.gate.wait(30)
        if self.fail_substr and self.fail_substr in rel.name:
            return {"success": False, "pages": 0, "error": "RuntimeError: boom", "total_elapsed_ms": 5.0}
        out = candidate_dir / rel.parent
        out.mkdir(parents=True, exist_ok=True)
        for pg in (1, 2):
            text = "" if self.empty_substr and self.empty_substr in rel.name else "md"
            (out / f"{rel.stem}_pg{pg}_repeat1.md").write_text(text, encoding="utf-8")
        return {"success": True, "pages": 2, "total_elapsed_ms": 200.0}


VALID_STDOUT = """
Candidate: axtract
  Average Score: 62.5% (95% CI: [50.0%, 70.0%]) over 40 tests.

============================================================
Final Summary with 95% Confidence Intervals:
axtract              : Average Score: 62.5% ± 4.5% (average of per-JSONL scores)
    present : 62.5% average pass rate over 40 tests

    Results by JSONL file:
        alpha.jsonl                   : 50.0% (5/10 tests)
        beta.jsonl                    : 75.0% (3/4 tests)
        table_tests.jsonl             : 60.0% (3/5 tests)
"""

INVALID_STDOUT = """
Candidate: axtract
  [ERROR] Candidate 'axtract' is missing MD repeats for alpha/doc03.pdf page 1 (expected files matching alpha/doc03_pg{page}_repeat*.md).
  [ERROR] Candidate 'axtract' is missing MD repeats for alpha/doc03.pdf page 1 (expected files matching alpha/doc03_pg{page}_repeat*.md).

============================================================
Final Summary with 95% Confidence Intervals:
axtract : Average Score: FAILED (errors)  (average of per-JSONL scores)
    present : 50.0% average pass rate over 4 tests

    Results by JSONL file:
        alpha.jsonl                   : 0.0% (0/4 tests)
"""


class FakeScore:
    """Mimics the scorer wrapper: writes outcomes.jsonl (one fail per document set) and prints official-style output.

    With failed documents present the real scorer prints FAILED (errors); `valid=None` picks that automatically."""

    def __init__(self, returncode: int = 0, valid: bool | None = None):
        self.returncode, self.valid, self.calls = returncode, valid, 0

    def __call__(self, scoring_dir: Path, outcomes: Path, html, skip_baseline: bool, cancel: threading.Event) -> dict:
        self.calls += 1
        assert skip_baseline is True  # matches the official full-run command
        rows = []
        missing = False
        for jsonl in sorted(scoring_dir.glob("*.jsonl")):
            for line in jsonl.read_text().splitlines():
                t = json.loads(line)
                md = scoring_dir / "axtract" / (t["pdf"][:-4] + "_pg1_repeat1.md")
                if not md.exists():
                    missing = True
                passed = md.exists() and t["id"].endswith("t0")
                rows.append({"id": t["id"], "pdf": t["pdf"], "page": t["page"], "type": t["type"], "source": jsonl.name,
                             "passed": passed, "explanation": "All repeats passed" if passed else ("Missing MD files" if not md.exists() else "No match found for x"),
                             "test": {"text": t.get("text")}})
        outcomes.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        if html:
            html.write_text("<html>report</html>", encoding="utf-8")
        valid = (not missing) if self.valid is None else self.valid
        return {"returncode": self.returncode, "stdout": VALID_STDOUT if valid else INVALID_STDOUT, "stderr": "", "cancelled": False}


def fake_git(_: Path) -> dict:
    return {"sha": "a" * 40, "branch": "feature/test", "dirty": False}


@pytest.fixture
def make_runner(settings):
    def factory(extract=None, score=None, git=fake_git, memory=lambda: 100_000):
        return BenchmarkRunner(settings, extract_fn=extract or FakeExtract(), score_fn=score or FakeScore(), git_fn=git, memory_fn=memory)

    return factory
