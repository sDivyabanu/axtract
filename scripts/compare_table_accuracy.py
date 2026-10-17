"""Compare frozen table samples without aggregating them into a full score."""
from __future__ import annotations
import argparse
import json
import statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def metrics(report):
    docs=report["candidate"]["documents"]
    rss=[d.get("process_lifetime_peak_working_set_bytes") for d in docs if d.get("process_lifetime_peak_working_set_bytes") is not None]
    cpu=[d.get("cpu_seconds") for d in docs]
    return {"source_commit":report["candidate"]["source_commit"],"pdfs":len(docs),
            "pages":sum(d["pages"] for d in docs),"errors":sum(len(d["errors"]) for d in docs),
            "wall_seconds":sum(d["seconds"] for d in docs),
            "cpu_seconds":sum(cpu) if all(c is not None for c in cpu) else None,
            "median_seconds_per_page":statistics.median(d["seconds"]/d["pages"] for d in docs),
            "peak_python_mib":max(d["peak_python_bytes"] for d in docs)/1048576,
            "process_lifetime_peak_mib":max(rss)/1048576 if rss else None,
            "scoring_seconds":report["scoring_seconds"]}


def compare(before,after):
    assert before["manifest_sha256"]==after["manifest_sha256"], "Manifest changed"
    for key in ["scorer_commit","scorer_tests_sha256","scorer_table_parsing_sha256"]:
        assert before[key]==after[key], "Official scorer changed"
    a={c["id"]:c for c in before["cases"]};b={c["id"]:c for c in after["cases"]}
    assert a.keys()==b.keys(), "Case selection changed"
    for key in a:
        assert (a[key]["pdf"],a[key]["type"])==(b[key]["pdf"],b[key]["type"])
    return {"before":before["summary"],"after":after["summary"],
            "gains":[c for key,c in b.items() if c["passed"] and not a[key]["passed"]],
            "regressions":[c for key,c in b.items() if not c["passed"] and a[key]["passed"]],
            "before_metrics":metrics(before),"after_metrics":metrics(after)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--before-label",default="verified_before")
    parser.add_argument("--after-label",default="verified_after")
    parser.add_argument("--output-prefix",type=Path,required=True)
    args=parser.parse_args()
    base=ROOT/"backend/data/table_accuracy_v2"
    result={"scope":"Native stage only, unchanged official case implementations; not full-corpus/full-pipeline accuracy", "splits":{}}
    failures=[]
    for split in ["original","development","heldout"]:
        def read(label):
            return json.loads((base/label/split/"official_case_results.json").read_text(encoding="utf-8"))
        before,after=read(args.before_label),read(args.after_label)
        result["splits"][split]=compare(before,after)
        failures.extend({"split":split,**c} for c in after["cases"] if not c["passed"])
    prefix=args.output_prefix
    prefix.parent.mkdir(parents=True,exist_ok=True)
    with Path(str(prefix)+".json").open("x",encoding="utf-8") as f:json.dump(result,f,indent=2)
    with Path(str(prefix)+"_failed_cases.jsonl").open("x",encoding="utf-8") as f:
        for failure in failures:f.write(json.dumps(failure,ensure_ascii=False)+"\n")
    lines=["# Frozen AXTRACT table sample comparison", "", result["scope"], "", "| Sample | Tables before | Tables after | Gained cases | Lost cases |", "|---|---:|---:|---:|---:|"]
    for split,r in result["splits"].items():
        a,b=r["before"]["tables"],r["after"]["tables"]
        lines.append(f"| {split} | {a['passed']}/{a['total']} | {b['passed']}/{b['total']} | {len(r['gains'])} | {len(r['regressions'])} |")
    lines += ["", "## Categories and performance", ""]
    for split,r in result["splits"].items():
        lines += [f"### {split}", "", "| Category | Before | After |", "|---|---:|---:|"]
        for cat,a in r["before"].items():
            b=r["after"][cat];lines.append(f"| {cat} | {a['passed']}/{a['total']} | {b['passed']}/{b['total']} |")
        lines += ["", "```json",json.dumps({"before":r["before_metrics"],"after":r["after_metrics"]},indent=2),"```", "", "Gained IDs:", ""]
        lines += ["- `"+c["id"]+"`" for c in r["gains"]] or ["None."]
        lines += ["", "Regressed IDs:", ""]
        lines += ["- `"+c["id"]+"`" for c in r["regressions"]] or ["None."]
        lines += [""]
    lines += ["## Limits", "", "All runs are sequential with tracemalloc. CPU seconds cover the extraction process; wall time includes writes and scheduling delays. Peak working set is the Windows process lifetime peak, including native allocations; per-document values are cumulative. Traced Python memory excludes native memory. This single paired run cannot establish a speedup; concurrent laptop activity and caches affect timing. Held-out selection was frozen independently and its case contents were not used for implementation, but aggregate outcomes were checked repeatedly, so this is validation rather than a pristine final blind estimate.", ""]
    with Path(str(prefix)+".md").open("x",encoding="utf-8") as f:f.write("\n".join(lines))
    print(json.dumps({split:{"tables":r["after"]["tables"],"gains":len(r["gains"]),"regressions":len(r["regressions"])} for split,r in result["splits"].items()}))

if __name__=="__main__":main()
