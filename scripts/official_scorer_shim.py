"""Run the unchanged official scorer (olmocr.bench.benchmark) on Windows.

The scorer keys its per-PDF Markdown lookup on `os.path.relpath(...)`, which is `category\\name.pdf` on Windows, while every test
refers to its PDF as `category/name.pdf`. Nothing then matches and the candidate is reported as "FAILED (errors)" with 0%, whatever
it contains. This launcher makes `os.path.relpath` return forward slashes and then runs the scorer's own `main()` untouched: no
scorer file is edited and no test, threshold or comparison is changed. On Linux/macOS it is a no-op.

  python official_scorer_shim.py <the scorer's own arguments>   # run with the scorer interpreter, scorer on PYTHONPATH
"""
import os
import runpy
import sys

_relpath = os.path.relpath


def relpath(path, start=os.curdir):
    return _relpath(path, start).replace(os.sep, "/")


os.path.relpath = relpath
sys.argv = ["olmocr.bench.benchmark", *sys.argv[1:]]
runpy.run_module("olmocr.bench.benchmark", run_name="__main__")
