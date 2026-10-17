"""Exact assertion-ID comparison of two official_case_results JSON files (read-only).
usage: compare_case_results.py BEFORE.json AFTER.json [--show N]
Prints per-category pass counts, IDs newly passing, IDs newly failing, and IDs missing from either side.
"""
import json
import sys
from collections import Counter

a_path, b_path = sys.argv[1], sys.argv[2]
show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 40
a = {c["id"]: c for c in json.load(open(a_path, encoding="utf-8"))["cases"]}
b = {c["id"]: c for c in json.load(open(b_path, encoding="utf-8"))["cases"]}
common = a.keys() & b.keys()
gained = sorted(i for i in common if b[i]["passed"] and not a[i]["passed"])
lost = sorted(i for i in common if a[i]["passed"] and not b[i]["passed"])


def cat(i, d):
    return d[i]["pdf"].split("/")[0]


print(f"before={sum(c['passed'] for c in a.values())}/{len(a)} after={sum(c['passed'] for c in b.values())}/{len(b)} "
      f"only_before={len(a.keys() - b.keys())} only_after={len(b.keys() - a.keys())}")
print("newly passing:", len(gained), dict(Counter(cat(i, b) for i in gained)))
print("newly failing:", len(lost), dict(Counter(cat(i, a) for i in lost)))
for label, ids, d in (("GAINED", gained, b), ("LOST", lost, a)):
    for i in ids[:show]:
        print(f"  {label} {i}  [{d[i]['pdf']}]")
