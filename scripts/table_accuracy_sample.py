"""Reproducible, sequential native-table stage sample; not a full benchmark.

Extraction uses the app Python. Scoring uses the unchanged official scorer Python.
See docs/TABLE_ACCURACY.md for commands and scope.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def process_peak_working_set():
    """Windows process lifetime peak RAM, including native allocations."""
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in
                ("peak", "working", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1), ctypes.byref(counters), counters.cb)
        return counters.peak if ok else None
    except (AttributeError, OSError):
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["select", "extract", "score"])
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--selection-config", type=Path, default=ROOT/"evaluation/table_accuracy_selection.json")
    parser.add_argument("--source-commit", help="Read extraction/rendering modules from a local commit without changing the checkout")
    parser.add_argument("--scorer", type=Path)
    parser.add_argument("--scorer-site-packages", type=Path)
    args = parser.parse_args()
    if args.source_commit:
        args.source_commit = subprocess.check_output(
            ["git", "rev-parse", args.source_commit], cwd=ROOT, text=True
        ).strip()
    data = args.data_root
    if args.action == "select":
        config = json.loads(args.selection_config.read_text(encoding="utf-8"))
        chosen = list(config["pdfs"])
        all_cases = []
        for name, expected_hash in config["case_source_hashes"].items():
            assert digest(data/name) == expected_hash, f"Case source changed: {name}"
            all_cases.extend(json.loads(l) for l in (data/name).read_text(encoding="utf-8").splitlines() if l.strip())
        for pdf, expected_hash in config["pdfs"].items():
            assert digest(data/"pdfs"/pdf) == expected_hash, f"PDF changed: {pdf}"
        selected = [c for c in all_cases if c["pdf"] in chosen]
        result = {**config, "cases": selected}
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("x", encoding="utf-8") as f: json.dump(result, f, indent=2)
        print(json.dumps({"pdfs":len(chosen), "cases":len(selected)}))
        return
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.action == "extract":
        sys.path.insert(0, str(ROOT/"backend"))
        module_paths = {
            "extractors.table_geometry": "backend/extractors/table_geometry.py",
            "extractors.text_math": "backend/extractors/text_math.py",
            "extractors.pymupdf_extractor": "backend/extractors/pymupdf_extractor.py",
            "services.table_service": "backend/services/table_service.py",
            "services.layout_service": "backend/services/layout_service.py",
            "services.markdown_service": "backend/services/markdown_service.py",
        }
        module_hashes = {}
        for name, file in module_paths.items():
            if args.source_commit:
                saved = subprocess.run(["git", "show", f"{args.source_commit}:{file}"], cwd=ROOT, capture_output=True)
                if saved.returncode and name in ("extractors.table_geometry", "extractors.text_math"):
                    continue  # The original measured revisions predate this helper.
                saved.check_returncode()
                source = saved.stdout
            else:
                source = (ROOT/file).read_bytes()
            module_hashes[file] = hashlib.sha256(source).hexdigest()
            if args.source_commit:
                import types
                module = types.ModuleType(name)
                module.__file__ = str(ROOT/file)
                module.__package__ = name.rpartition(".")[0]
                sys.modules[name] = module
                exec(compile(source, str(ROOT/file), "exec"), module.__dict__)
        from extractors.pymupdf_extractor import PyMuPDFExtractor
        from services.table_service import enhance_table_block, merge_cross_page_tables
        from services.layout_service import assign_reading_order
        from services.markdown_service import blocks_to_markdown
        args.output.mkdir(parents=True, exist_ok=False)
        from importlib.metadata import version
        timings = []
        for pdf, expected_hash in manifest["pdfs"].items():
            source = data/"pdfs"/pdf
            assert digest(source) == expected_hash, f"PDF changed: {pdf}"
            tracemalloc.start()
            start = time.perf_counter()
            cpu_start = time.process_time()
            result = PyMuPDFExtractor().extract(source)
            blocks = assign_reading_order(merge_cross_page_tables([enhance_table_block(b) for b in result.blocks]))
            for page in range(1, result.page_count+1):
                path = args.output/Path(pdf).parent/(Path(pdf).stem+f"_pg{page}_repeat1.md")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(blocks_to_markdown([b for b in blocks if b.page == page]), encoding="utf-8")
            elapsed = time.perf_counter()-start
            cpu_seconds = time.process_time()-cpu_start
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            timings.append({"pdf":pdf,"seconds":elapsed,"cpu_seconds":cpu_seconds,"process_lifetime_peak_working_set_bytes":process_peak_working_set(),"pages":result.page_count,"peak_python_bytes":peak,"errors":[str(e) for e in result.errors]})
            print(f"{pdf}: {elapsed:.3f}s", flush=True)
        (args.output/"performance.json").write_text(json.dumps({"commit":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"dirty":subprocess.check_output(["git","status","--porcelain"],cwd=ROOT,text=True),"scope":manifest["scope"],"source_commit":args.source_commit or "working-tree", "source_sha256":module_hashes,"python":sys.version,"packages":{name:version(name) for name in ["PyMuPDF","pydantic"]},"documents":timings},indent=2),encoding="utf-8")
    else:
        if args.scorer_site_packages: sys.path.insert(0,str(args.scorer_site_packages))
        sys.path.insert(0,str(args.scorer))
        for name, expected_hash in manifest["case_source_hashes"].items():
            assert digest(data/name) == expected_hash, f"Case source changed: {name}"
        from olmocr.bench.tests import load_single_test
        scored = []
        start = time.perf_counter()
        for case in manifest["cases"]:
            path = args.output/Path(case["pdf"]).parent/(Path(case["pdf"]).stem+f"_pg{case['page']}_repeat1.md")
            test = load_single_test(case)
            passed, explanation = test.run(path.read_text(encoding="utf-8")) if path.exists() else (False,"Missing output")
            scored.append({"id":case["id"],"pdf":case["pdf"],"type":case["type"],"passed":bool(passed),"explanation":explanation})
        summary = {cat:{"passed":sum(r["passed"] for r in scored if r["pdf"].split('/')[0]==cat),"total":sum(r["pdf"].split('/')[0]==cat for r in scored)} for cat in sorted({r["pdf"].split('/')[0] for r in scored})}
        report = {"scope":manifest["scope"],"scorer_commit":subprocess.check_output(["git","-C",str(args.scorer),"rev-parse","HEAD"],text=True).strip(),"scorer_tests_sha256":digest(args.scorer/"olmocr/bench/tests.py"),"scorer_table_parsing_sha256":digest(args.scorer/"olmocr/bench/table_parsing.py"),"manifest_sha256":digest(args.manifest),"candidate":json.loads((args.output/"performance.json").read_text()),"scoring_seconds":time.perf_counter()-start,"summary":summary,"cases":scored}
        with (args.output/"official_case_results.json").open("x",encoding="utf-8") as f: json.dump(report,f,indent=2)
        print(json.dumps(summary))

if __name__ == "__main__": main()
