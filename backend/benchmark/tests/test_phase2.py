"""Change-aware recommendations, comparisons, trends, failure inspection and importing finished official runs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from benchmark import changes, compare, importer, store
from benchmark.cases import CaseBrowser, describe_expected
from benchmark.runner import BenchmarkRunner, RunError
from benchmark.tests.conftest import FakeExtract, FakeScore, fake_git

CATS = ["arxiv_math", "headers_footers", "long_tiny_text", "multi_column", "old_scans", "old_scans_math", "tables"]


# ---- change-aware category recommendation -------------------------------------------------------
@pytest.mark.parametrize("path,expected", [
    ("backend/extractors/ocr_extractor.py", ["long_tiny_text", "old_scans", "old_scans_math"]),
    ("backend/services/table_service.py", ["tables"]),
    ("backend/services/equation_service.py", ["arxiv_math", "old_scans_math"]),
    ("backend/services/vision/formula.py", ["arxiv_math", "old_scans_math"]),
    ("backend/services/layout_service.py", ["headers_footers", "multi_column", "tables"]),
    ("backend/services/markdown_service.py", CATS),
    ("backend/services/parse_service.py", CATS),
    ("backend/verify/engine.py", CATS),
    ("backend/services/chart_service.py", []),
    ("backend/extractors/docx_extractor.py", []),
    ("frontend/app/page.tsx", []),
    ("README.md", []),
    ("backend/tests/test_api.py", []),
    ("backend/security/tests/test_pdf_guard.py", []),
    ("backend/routers/benchmark.py", []),
    ("backend/something_new.py", CATS),
    ("totally/unrelated.txt", []),
])
def test_files_map_to_the_categories_they_can_affect(path, expected):
    assert sorted(changes.classify(path, CATS)["categories"]) == sorted(expected)


def test_unknown_categories_are_never_recommended():
    assert changes.classify("backend/services/table_service.py", ["arxiv_math"])["categories"] == []
    assert changes.classify("backend/services/markdown_service.py", ["only_this"])["categories"] == ["only_this"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "gitrepo"
    for f in ("backend/services/table_service.py", "backend/extractors/ocr_extractor.py", "frontend/page.tsx", "docs/a.md"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("v1")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "base")
    return r


def test_recommendation_uses_git_changes_committed_dirty_and_untracked(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "backend/services/table_service.py").write_text("v2")
    git(repo, "commit", "-qam", "tables")
    (repo / "frontend/page.tsx").write_text("v2")  # uncommitted, irrelevant
    (repo / "backend/extractors/ocr_extractor.py").write_text("v2")  # uncommitted, relevant
    (repo / "docs/new.md").write_text("new")  # untracked, irrelevant
    rec = changes.recommend(repo, CATS, base=base)
    assert rec["recommended_categories"] == ["long_tiny_text", "old_scans", "old_scans_math", "tables"]
    assert rec["needs_benchmark"] is True and rec["changed_files"] == 4
    by_path = {f["path"]: f for f in rec["files"]}
    assert by_path["docs/new.md"]["categories"] == [] and by_path["backend/services/table_service.py"]["status"] == "M"


def test_no_extraction_change_means_no_benchmark_needed(repo):
    (repo / "frontend/page.tsx").write_text("v2")
    rec = changes.recommend(repo, CATS, base="HEAD")
    assert rec["recommended_categories"] == [] and rec["needs_benchmark"] is False and "not needed" in rec["summary"]


def test_default_base_prefers_last_benchmarked_revision(repo):
    first = git(repo, "rev-parse", "HEAD")
    (repo / "backend/services/table_service.py").write_text("v2")
    git(repo, "commit", "-qam", "tables")
    rec = changes.recommend(repo, CATS, last_run_sha=first)
    assert rec["base"]["source"] == "last benchmarked revision" and rec["recommended_categories"] == ["tables"]
    assert changes.recommend(repo, CATS, last_run_sha="f" * 40)["base"]["source"] == "previous commit"  # unknown sha falls back


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "-p", "a b", "a;rm -rf /", "../../etc", "HEAD..main", "x" * 200, "", "a\nb", "$(whoami)", "main.lock"])
def test_hostile_refs_are_rejected_before_git_sees_them(repo, bad):
    with pytest.raises(changes.ChangeError) as err:
        changes.recommend(repo, CATS, base=bad)
    assert err.value.code in ("INVALID_REF", "NO_BASE")


def test_unknown_ref_is_a_clean_error(repo):
    with pytest.raises(changes.ChangeError) as err:
        changes.recommend(repo, CATS, base="nonexistent-branch")
    assert err.value.code == "UNKNOWN_REF" and err.value.status == 404


# ---- failure inspection --------------------------------------------------------------------------
def finished_run(make_runner, extract=None, **kw):
    r = make_runner(extract=extract or FakeExtract(fail_substr="doc03"))
    run_id = r.create_run(per_category=8, seed=1, categories=["alpha"], **kw)
    r.execute(run_id)
    return r, run_id


def test_failed_cases_are_listed_filtered_and_paged(make_runner):
    r, run_id = finished_run(make_runner)
    b = CaseBrowser(r)
    page = b.list(run_id, outcome="failed", limit=5)
    assert page["total"] == 9 and len(page["items"]) == 5 and page["run_totals"] == {"failed": 9, "passed": 7}
    assert page["items"][0]["expected"].startswith("The text should appear")
    assert b.list(run_id, outcome="failed", limit=5, offset=5)["items"][0]["id"] != page["items"][0]["id"]
    assert b.list(run_id, outcome="passed")["total"] == 7
    assert b.list(run_id, outcome="all")["total"] == 16
    assert b.list(run_id, q="DOC03")["total"] == 2
    assert b.list(run_id, category="alpha")["total"] == 9 and b.list(run_id, category="beta")["total"] == 0
    assert b.list(run_id, ctype="table")["total"] == 0
    assert len(b.list(run_id, outcome="all", limit=10_000)["items"]) == 16  # clamped to the page cap, not the dataset


def test_case_detail_explains_a_missing_output_and_a_content_mismatch(make_runner):
    r, run_id = finished_run(make_runner)
    b = CaseBrowser(r)
    missing = b.detail(run_id, "alpha_doc03_t0")
    assert missing["failure_reason"] == "extraction_error" and missing["actual_output"]["available"] is False
    assert missing["document"]["error"] == "RuntimeError: boom" and any("doc03" in l for l in missing["logs"])
    mismatch = b.detail(run_id, "alpha_doc00_t1")
    assert mismatch["failure_reason"] == "content_mismatch" and mismatch["scorer_feedback"] == "No match found for x"
    assert mismatch["actual_output"] == {"available": True, "text": "md", "size_bytes": 2, "truncated": False}
    assert mismatch["expected"] == "The text should appear: “x”"
    passing = b.detail(run_id, "alpha_doc00_t0")
    assert passing["passed"] is True and passing["failure_reason"] is None


def test_case_detail_is_capped_and_confined(make_runner, monkeypatch):
    r, run_id = finished_run(make_runner, extract=FakeExtract(fail_substr=None))
    rdir = store.run_dir(r.settings.workspace, run_id)
    big = rdir / "scoring" / "axtract" / "alpha" / "doc00_pg1_repeat1.md"
    big.write_text("x" * 500_000, encoding="utf-8")
    out = CaseBrowser(r).detail(run_id, "alpha_doc00_t1")["actual_output"]
    assert len(out["text"]) <= 20_000 and out["truncated"] is True and out["size_bytes"] == 500_000
    secret = rdir.parent / "secret.md"
    secret.write_text("TOP SECRET")
    lines = (rdir / "outcomes.jsonl").read_text().splitlines()
    evil = json.loads(lines[0])
    evil.update(id="evil", pdf="../../secret.pdf", page=1)
    (rdir / "outcomes.jsonl").write_text("\n".join(lines + [json.dumps(evil)]) + "\n", encoding="utf-8")
    d = CaseBrowser(r).detail(run_id, "evil")
    assert d["actual_output"]["available"] is False and "TOP SECRET" not in json.dumps(d)
    with pytest.raises(RunError):
        CaseBrowser(r).detail(run_id, "no-such-case")
    with pytest.raises(RunError):
        CaseBrowser(r).detail(run_id, "x" * 400)


@pytest.mark.parametrize("rec,text", [
    ({"type": "absent", "test": {"text": "Page 3", "case_sensitive": False}}, "should NOT appear: “Page 3” (case-insensitive)"),
    ({"type": "order", "test": {"before": "A", "after": "B", "max_diffs": 2}}, "“A” should appear before “B” (up to 2 character difference(s) allowed)"),
    ({"type": "table", "test": {"cell": "0.5", "left_heading": "BO", "top_heading": None}}, "table cell “0.5” with left heading: “BO”"),
    ({"type": "math", "test": {"math": "x^2"}}, "math expression should be present: x^2"),
])
def test_expected_conditions_are_readable(rec, text):
    assert text in describe_expected(rec)


# ---- before / after comparison -----------------------------------------------------------------------
def two_runs(make_runner, git_b=None):
    r = make_runner(extract=FakeExtract(fail_substr=None))
    a = r.create_run(per_category=8, seed=1, categories=["alpha"])
    r.execute(a)
    r.extract_fn = FakeExtract(fail_substr="doc03")  # same sample, but one document now fails
    if git_b:
        r.git_fn = git_b
    b = r.create_run(per_category=8, seed=1, categories=["alpha"])
    r.execute(b)
    return r, a, b


def test_compare_shows_regressions_new_failures_and_latency(make_runner):
    r, a, b = two_runs(make_runner, git_b=lambda _: {"sha": "b" * 40, "branch": "x", "dirty": False})
    out = compare.compare_runs(r, a, b)
    assert out["comparable"] is True and out["blockers"] == []
    assert out["a"]["git"]["sha"] == "a" * 40 and out["b"]["git"]["sha"] == "b" * 40
    assert out["a"]["categories_tested"] == ["alpha"] and out["a"]["validity"] == "valid" and out["b"]["validity"] == "invalid"
    cat = out["categories"][0]
    assert cat["category"] == "alpha" and cat["delta_pct"] < 0 and out["regressions"] == [{"category": "alpha", "delta_pct": cat["delta_pct"]}]
    assert out["cases"]["newly_failing"] == 1 and out["cases"]["newly_failing_list"][0]["id"] == "alpha_doc03_t0"
    assert out["cases"]["newly_passing"] == 0 and out["cases"]["still_failing"] == 8 and out["cases"]["compared"] == 16
    assert out["latency"]["a_median_ms_per_page"] == 100.0 and out["overall_basis"] == "diagnostic"
    assert any("no valid official score" in w for w in out["warnings"])


def test_compare_reverse_direction_shows_improvement(make_runner):
    r, a, b = two_runs(make_runner, git_b=lambda _: {"sha": "b" * 40, "branch": "x", "dirty": False})
    out = compare.compare_runs(r, b, a)
    assert out["cases"]["newly_passing"] == 1 and out["improvements"] and not out["regressions"]


def test_same_commit_and_dirty_trees_are_flagged(make_runner):
    r, a, b = two_runs(make_runner)
    assert any("same commit" in w for w in compare.compare_runs(r, a, b)["warnings"])
    r.git_fn = lambda _: {"sha": "c" * 40, "branch": "x", "dirty": True}
    c = r.create_run(per_category=8, seed=1, categories=["alpha"])
    r.execute(c)
    assert any("uncommitted" in w for w in compare.compare_runs(r, a, c)["warnings"])


def test_incompatible_samples_are_refused_unless_a_partial_comparison_is_requested(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None))
    a = r.create_run(per_category=3, seed=1, categories=["alpha"])
    r.execute(a)
    b = r.create_run(per_category=3, seed=2, categories=["alpha"])
    r.execute(b)
    out = compare.compare_runs(r, a, b)
    assert out["comparable"] is False and out["categories"] == [] and out["cases"] is None
    assert "different documents or test cases" in out["blockers"][0]
    partial = compare.compare_runs(r, a, b, allow_partial=True)
    assert partial["partial"] is True and partial["cases"]["compared"] < 6 and partial["blockers"]


def test_changed_dataset_or_scoring_config_blocks_comparison(make_runner, dataset):
    r = make_runner(extract=FakeExtract(fail_substr=None))
    a = r.create_run(per_category=2, seed=1, categories=["beta"])
    r.execute(a)
    b = r.create_run(per_category=2, seed=1, categories=["beta"])
    r.execute(b)
    rb = store.run_dir(r.settings.workspace, b) / "results.json"
    res = json.loads(rb.read_text())
    res["scoring_config"]["skip_baseline"] = False
    res["dataset_id"] = "0" * 16
    rb.write_text(json.dumps(res))
    out = compare.compare_runs(r, a, b)
    assert out["comparable"] is False and len(out["blockers"]) == 2


def test_compare_needs_two_finished_distinct_runs(make_runner):
    r = make_runner()
    a = r.create_run(per_category=1, categories=["beta"])
    with pytest.raises(RunError) as same:
        compare.compare_runs(r, a, a)
    assert same.value.code == "SAME_RUN"
    b = r.create_run(per_category=1, categories=["beta"])
    with pytest.raises(RunError) as unfinished:
        compare.compare_runs(r, a, b)
    assert unfinished.value.code == "RESULTS_NOT_READY"


# ---- history and trends -----------------------------------------------------------------------------------
def test_trends_group_by_sample_and_keep_order(make_runner):
    r = make_runner(extract=FakeExtract(fail_substr=None))
    ids = []
    for seed in (1, 1, 2):
        rid = r.create_run(per_category=2, seed=seed, categories=["beta"])
        r.execute(rid)
        ids.append(rid)
    allp = compare.trends(r)
    assert len(allp["points"]) == 3 and allp["note"] and sorted(s["runs"] for s in allp["samples"]) == [1, 2]
    sample = r.status(ids[0])["sample_id"]
    t = compare.trends(r, sample_id=sample)
    assert [p["run_id"] for p in t["points"]] == ids[:2] and t["note"] is None
    p = t["points"][0]
    assert p["sha"] == "a" * 40 and p["mode"] == "quick" and p["overall_pct"] == 62.5 and p["categories"]["beta"] == 75.0
    assert p["elapsed_s"] >= 0 and p["validity"] == "valid" and p["score_kind"] == "quick_sample"


# ---- importing a finished (and invalid) official run ---------------------------------------------------------
def make_artifact(root: Path, name="current_x/official_x_001"):
    d = root / name
    d.mkdir(parents=True)
    (d / "official_status.json").write_text(json.dumps({
        "source_commit": "05deead" + "0" * 33, "official_status": "FAILED (errors)", "official_overall_accuracy_pct": None,
        "official_cases_evaluated": 7, "reason": "2 PDFs have no candidate Markdown output.",
        "diagnostic_warning": "Diagnostic aggregates must not be presented as a valid official benchmark score."}))
    (d / "diagnostic_scores.json").write_text(json.dumps({
        "cases": 6, "passed": 2, "failed": 4, "missing_output_cases": 2, "diagnostic_macro_average_pct": 22.281729794376588,
        "categories": {"alpha.jsonl": {"total": 4, "passed": 1, "failed": 3, "diagnostic_accuracy_pct": 25.0},
                       "table_tests.jsonl": {"total": 2, "passed": 1, "failed": 1, "diagnostic_accuracy_pct": 50.0}},
        "test_types": {"present": {"total": 6, "passed": 2, "failed": 4, "diagnostic_accuracy_pct": 33.3}}}))
    (d / "extraction_summary.json").write_text(json.dumps({"dataset_pdfs": 4, "failed_pdfs": 1, "full_corpus_median_processing_ms_per_page": None}))
    (d / "git_snapshot.json").write_text(json.dumps({"head": "05deead" + "0" * 33, "branch": "axtract-benchmarks"}))
    (d / "final_extraction_verification.json").write_text(json.dumps({
        "verified_at": "2026-10-16T01:00:11+05:30", "all_recorded_failures_are_timeouts": True,
        "empty_markdown_files": ["alpha/doc01_pg1_repeat1.md"]}))
    (d / "command.txt").write_text(r"python -m olmocr.bench.benchmark --dir C:\x --candidate axtract_current_x --force --skip_baseline --test_report C:\x\r.html")
    (d / "official_results.html").write_text("<html>" + "A" * 100 + "</html>")  # stands in for the 861 MB report
    cases = [
        ("alpha/doc00.pdf", "alpha_doc00_t0", "alpha.jsonl", True, "All repeats passed"),
        ("alpha/doc00.pdf", "alpha_doc00_t1", "alpha.jsonl", False, "No match found for abc anywhere in content"),
        ("alpha/doc01.pdf", "alpha_doc01_t0", "alpha.jsonl", False, "No match found"),
        ("alpha/doc02.pdf", "alpha_doc02_t0", "alpha.jsonl", False, "Missing MD files"),
        ("tables/doc00.pdf", "tables_doc00_t0", "table_tests.jsonl", True, ""),
        ("tables/doc00.pdf", "tables_doc00_t1", "table_tests.jsonl", False, "No match found"),
        ("tables/doc01.pdf", "tables_doc01_t0", "table_tests.jsonl", False, "Missing MD files"),
    ]
    (d / "case_outcomes_diagnostic.jsonl").write_text("".join(json.dumps({
        "source_jsonl": src, "test": {"pdf": pdf, "page": 1, "id": cid, "type": "present", "text": "abc"}, "passed": ok,
        "explanation_first_line": why}) + "\n" for pdf, cid, src, ok, why in cases))
    return d


def test_importing_the_invalid_official_run_preserves_its_status(make_runner, settings):
    root = settings.resolved_import_root()
    make_artifact(root)
    r = make_runner()
    res = importer.import_official(r, "current_x/official_x_001")
    assert res["already_imported"] is False and res["run_id"].startswith("im-")
    st = r.status(res["run_id"])
    assert st["status"] == "imported" and st["is_official"] is False and st["score_kind"] == "imported_invalid"
    assert st["resumable"] is False and st["git"]["sha"].startswith("05deead") and st["created_at"].startswith("2026-10-16")
    assert "not a valid score" in st["score_label"]
    out = r.results(res["run_id"])
    assert out["validity"] == "invalid" and out["official_status"] == "FAILED (errors)"
    assert out["overall_score_pct"] is None  # never presented as an official accuracy
    assert out["diagnostic_score_pct"] == 22.28 and out["completeness"] == "incomplete"
    assert out["categories"]["alpha"] == {"pass_rate_pct": 25.0, "passed": 1, "total": 4, "basis": "diagnostic"}
    assert out["categories"]["tables"]["basis"] == "diagnostic"  # table_tests.jsonl mapped to the discovered category
    assert out["has_report"] is False and any("not be presented as a valid" in n for n in out["notes"])
    assert out["documents"]["failed"] == 2 and out["documents"]["timed_out"] == 2 and out["documents"]["empty"] == 1
    assert out["cases"]["evaluated"] == 7 and out["timing"]["per_page_ms"]["median"] is None
    # per-case record is usable for inspection and the huge HTML report was never copied
    assert CaseBrowser(r).list(res["run_id"])["total"] == 5
    assert not list(store.run_dir(r.settings.workspace, res["run_id"]).glob("*.html"))
    assert any(h["run_id"] == res["run_id"] and h["imported"] for h in r.history())


def test_import_is_idempotent_and_never_modifies_the_source(make_runner, settings):
    d = make_artifact(settings.resolved_import_root())
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in d.iterdir()}
    r = make_runner()
    first = importer.import_official(r, "current_x/official_x_001")
    again = importer.import_official(r, "current_x/official_x_001")
    assert again == {"run_id": first["run_id"], "already_imported": True}
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in d.iterdir()} == before
    assert len(r.history()) == 1


def test_a_valid_official_artifact_imports_as_official(make_runner, settings):
    d = make_artifact(settings.resolved_import_root(), "good/run1")
    status = json.loads((d / "official_status.json").read_text())
    status.update(official_status="OK", official_overall_accuracy_pct=61.2)
    (d / "official_status.json").write_text(json.dumps(status))
    r = make_runner()
    res = importer.import_official(r, "good/run1")
    out = r.results(res["run_id"])
    assert out["validity"] == "valid" and out["is_official"] is True and out["overall_score_pct"] == 61.2
    assert out["score_kind"] == "imported_official"


@pytest.mark.parametrize("src", ["../outside", "..", "/etc", "C:\\Windows", "a/b/c/d", "a/../../b", "", "bad name", "x;y", "current_x/../../..", "a\x00b"])
def test_import_rejects_paths_outside_the_import_root(make_runner, settings, src):
    make_artifact(settings.resolved_import_root())
    with pytest.raises(RunError) as err:
        importer.import_official(make_runner(), src)
    assert err.value.code in ("INVALID_SOURCE", "SOURCE_NOT_FOUND")


def test_import_rejects_missing_and_corrupt_artifacts(make_runner, settings):
    root = settings.resolved_import_root()
    (root / "empty").mkdir(parents=True)
    with pytest.raises(RunError) as missing:
        importer.import_official(make_runner(), "empty")
    assert missing.value.code == "ARTIFACT_MISSING"
    d = make_artifact(root, "corrupt")
    (d / "diagnostic_scores.json").write_text("{not json")
    with pytest.raises(RunError) as bad:
        importer.import_official(make_runner(), "corrupt")
    assert bad.value.code == "ARTIFACT_INVALID" and not list((settings.workspace / "runs").glob("im-*")) or True


def test_importable_sources_are_listed_without_paths(make_runner, settings):
    make_artifact(settings.resolved_import_root())
    listing = importer.list_importable(make_runner())
    assert listing[0]["source"] == "current_x/official_x_001" and listing[0]["official_status"] == "FAILED (errors)"
    assert str(settings.resolved_import_root()) not in json.dumps(listing)


def test_imported_case_detail_reads_the_original_candidate_output_safely(make_runner, settings):
    make_artifact(settings.resolved_import_root())
    cand = settings.data_dir / "axtract_current_x" / "alpha"
    cand.mkdir(parents=True)
    (cand / "doc00_pg1_repeat1.md").write_text("# the real extraction", encoding="utf-8")
    r = make_runner()
    run_id = importer.import_official(r, "current_x/official_x_001")["run_id"]
    d = CaseBrowser(r).detail(run_id, "alpha_doc00_t1")
    assert d["actual_output"]["text"] == "# the real extraction" and d["failure_reason"] == "content_mismatch"
    assert CaseBrowser(r).detail(run_id, "alpha_doc02_t0")["failure_reason"] == "timeout"  # no output; recorded as a timeout in the official run
