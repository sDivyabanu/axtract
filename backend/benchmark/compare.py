"""Before/after comparison of two runs and score trends over history.

Two runs are only *directly* comparable when they used the same dataset files, the same scoring configuration
and the same set of test cases. Otherwise the comparison is refused (with the reasons), unless the caller
explicitly asks for a partial comparison, which is then restricted to the cases both runs contain and labelled.
"""

from __future__ import annotations

from .runner import BenchmarkRunner, RunError
from .scoring import iter_outcomes

MAX_LISTED = 200
EPS = 1e-9


def _summary(runner: BenchmarkRunner, run_id: str) -> tuple[dict, dict]:
    st = runner.status(run_id)
    res = runner.results(run_id)  # raises RESULTS_NOT_READY for runs that have not finished scoring
    return {
        "run_id": run_id, "created_at": st["created_at"], "git": st["git"], "mode": st["mode"], "score_kind": st["score_kind"],
        "score_label": st["score_label"], "is_official": st["is_official"], "validity": res.get("validity"),
        "completeness": res.get("completeness"), "overall_score_pct": res.get("overall_score_pct"),
        "diagnostic_score_pct": res.get("diagnostic_score_pct"), "categories_tested": list((res.get("categories") or {})),
        "documents": res.get("documents"), "cases": res.get("cases"), "elapsed_s": st["elapsed_s"],
        "median_ms_per_page": ((res.get("timing") or {}).get("per_page_ms") or {}).get("median"),
        "total_extraction_ms": (res.get("timing") or {}).get("total_extraction_ms"),
        "sample_id": res.get("sample_id"), "dataset_id": res.get("dataset_id"), "scoring_config": res.get("scoring_config"),
        "git_changed_since_creation": bool(st.get("git_changed_since_creation")),
    }, res


def _pct(res: dict, cat: str) -> dict | None:
    return (res.get("categories") or {}).get(cat)


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round(b - a, 2)


def compare_runs(runner: BenchmarkRunner, run_a: str, run_b: str, allow_partial: bool = False) -> dict:
    if run_a == run_b:
        raise RunError("SAME_RUN", "Pick two different runs to compare.")
    sa, ra = _summary(runner, run_a)
    sb, rb = _summary(runner, run_b)

    blockers: list[str] = []
    warnings: list[str] = []
    if sa["dataset_id"] != sb["dataset_id"]:
        blockers.append("The runs used different benchmark dataset files.")
    if sa["scoring_config"] != sb["scoring_config"]:
        blockers.append("The runs used different scoring configurations.")
    if sa["sample_id"] != sb["sample_id"]:
        blockers.append("The runs tested different documents or test cases.")
    if sa["mode"] != sb["mode"]:
        blockers.append("A quick sample cannot be compared directly with a full run.")
    for s in (sa, sb):
        if s["validity"] != "valid":
            warnings.append(f"Run {s['run_id']} has no valid official score; its percentages are diagnostic (outputs were missing).")
        if s["git"].get("dirty"):
            warnings.append(f"Run {s['run_id']} was made with uncommitted changes, so its commit SHA does not fully describe the code.")
        if s["git_changed_since_creation"]:
            warnings.append(f"Run {s['run_id']} was resumed on a different revision.")
    if sa["git"].get("sha") and sa["git"].get("sha") == sb["git"].get("sha"):
        warnings.append("Both runs are on the same commit; differences are noise or caused by uncommitted changes.")

    comparable = not blockers
    out = {"a": sa, "b": sb, "comparable": comparable, "partial": False, "blockers": blockers, "warnings": warnings,
           "categories": [], "regressions": [], "improvements": [], "cases": None, "latency": None, "overall_delta_pct": None}
    if not comparable and not allow_partial:
        return out
    out["partial"] = not comparable

    # Case-level: streamed, restricted to the cases present in both runs.
    ids_a: dict[str, bool] = {}
    meta: dict[str, dict] = {}
    jsonl_to_cat_a = {j: c for c, j in runner.load_run(run_a).get("category_jsonl", {}).items()}
    for rec in iter_outcomes(runner._dir(run_a) / "outcomes.jsonl"):
        ids_a[rec["id"]] = bool(rec.get("passed"))
        meta[rec["id"]] = {"id": rec["id"], "pdf": rec.get("pdf"), "type": rec.get("type"),
                           "category": jsonl_to_cat_a.get(rec.get("source") or "")}
    newly_pass, newly_fail, common, still_fail, still_pass = [], [], 0, 0, 0
    only_b = 0
    cat_counts: dict[str, dict] = {}
    for rec in iter_outcomes(runner._dir(run_b) / "outcomes.jsonl"):
        cid = rec["id"]
        if cid not in ids_a:
            only_b += 1
            continue
        common += 1
        before, after = ids_a[cid], bool(rec.get("passed"))
        m = meta[cid]
        c = cat_counts.setdefault(m["category"] or "?", {"a_pass": 0, "b_pass": 0, "n": 0})
        c["n"] += 1
        c["a_pass"] += before
        c["b_pass"] += after
        if after and not before:
            newly_pass.append(m)
        elif before and not after:
            newly_fail.append(m)
        elif after:
            still_pass += 1
        else:
            still_fail += 1
    out["cases"] = {"compared": common, "only_in_a": len(ids_a) - common, "only_in_b": only_b,
                    "newly_passing": len(newly_pass), "newly_failing": len(newly_fail),
                    "still_passing": still_pass, "still_failing": still_fail,
                    "newly_passing_list": newly_pass[:MAX_LISTED], "newly_failing_list": newly_fail[:MAX_LISTED]}

    cats = sorted(set(ra.get("categories") or {}) | set(rb.get("categories") or {}))
    for c in cats:
        a, b = _pct(ra, c), _pct(rb, c)
        row = {"category": c, "a_pct": a and a["pass_rate_pct"], "b_pct": b and b["pass_rate_pct"],
               "delta_pct": _delta(a and a["pass_rate_pct"], b and b["pass_rate_pct"]),
               "a_passed": a and a["passed"], "a_total": a and a["total"], "b_passed": b and b["passed"], "b_total": b and b["total"]}
        if c in cat_counts:
            row["compared_cases"] = cat_counts[c]["n"]
        out["categories"].append(row)
        if row["delta_pct"] is not None:
            if row["delta_pct"] < -EPS:
                out["regressions"].append({"category": c, "delta_pct": row["delta_pct"]})
            elif row["delta_pct"] > EPS:
                out["improvements"].append({"category": c, "delta_pct": row["delta_pct"]})
    ka = sa["overall_score_pct"] if sa["overall_score_pct"] is not None else sa["diagnostic_score_pct"]
    kb = sb["overall_score_pct"] if sb["overall_score_pct"] is not None else sb["diagnostic_score_pct"]
    out["overall_delta_pct"] = _delta(ka, kb)
    out["overall_basis"] = "official" if sa["validity"] == sb["validity"] == "valid" else "diagnostic"
    ma, mb = sa["median_ms_per_page"], sb["median_ms_per_page"]
    out["latency"] = {"a_median_ms_per_page": ma, "b_median_ms_per_page": mb, "delta_ms": _delta(ma, mb),
                      "delta_pct": round(100.0 * (mb - ma) / ma, 1) if ma and mb is not None else None,
                      "a_total_extraction_ms": sa["total_extraction_ms"], "b_total_extraction_ms": sb["total_extraction_ms"]}
    return out


def trends(runner: BenchmarkRunner, sample_id: str | None = None, limit: int = 50) -> dict:
    """Chronological series of finished runs. With `sample_id` the series is a like-for-like trend line."""
    rows = [h for h in runner.history(200) if h["status"] in ("completed", "imported") and h.get("category_scores") is not None]
    if sample_id:
        rows = [h for h in rows if h["sample_id"] == sample_id]
    rows = sorted(rows, key=lambda h: h["created_at"] or "")[-limit:]
    samples: dict[str, int] = {}
    for h in runner.history(200):
        if h["status"] in ("completed", "imported") and h.get("sample_id"):
            samples[h["sample_id"]] = samples.get(h["sample_id"], 0) + 1
    points = [{"run_id": h["run_id"], "created_at": h["created_at"], "sha": (h["git"] or {}).get("sha"),
               "dirty": (h["git"] or {}).get("dirty"), "mode": h["mode"], "score_kind": h["score_kind"],
               "validity": h["validity"], "sample_id": h["sample_id"],
               "overall_pct": h["overall_score_pct"], "diagnostic_pct": h["diagnostic_score_pct"],
               "categories": h["category_scores"], "elapsed_s": h["elapsed_s"], "median_ms_per_page": h["median_ms_per_page"],
               "imported": bool(h.get("imported"))} for h in rows]
    return {"sample_id": sample_id, "points": points,
            "samples": [{"sample_id": s, "runs": n} for s, n in sorted(samples.items(), key=lambda kv: -kv[1])],
            "note": None if sample_id else "Pick a sample to see a like-for-like trend; runs on different samples are not comparable."}
