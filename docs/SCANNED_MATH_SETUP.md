# Scanned-math setup (offline, reproducible)

Scanned-math recognition (`AXTRACT_SCANNED_MATH=1`) reads equations on scanned pages with Pix2Text-MFR. It is **off by default**.
Nothing here downloads anything at runtime. Packages come from `pip` once, at install time; model files are placed on the
machine by you and verified by SHA-256.

## What is required

| Item | Version / identity | Licence | Where it comes from |
|---|---|---|---|
| Python | 3.14.6 (the measured interpreter) | PSF | system |
| `onnxruntime` | 1.30.0 | MIT | `pip install -r backend/requirements.txt` |
| `tokenizers` | 0.23.3 | Apache-2.0 | same (previously undeclared) |
| `numpy`, `Pillow`, `latex2mathml` | 2.5.3, 12.3.0, 3.81.1 | BSD / HPND / MIT | same |
| Pix2Text-MFR weights | `breezedeus/pix2text-mfr`, ONNX encoder + decoder, 8 files, 118 MB | MIT (model card, checked 2026-10-18) | **you supply** (see below) |

Not needed for scanned math: `rapid-latex-ocr`, `chardet` and the `latex_ocr` weights (the older pix2tex path, off for scans) and
`fastembed` / `pypdf` (RAG layer). They live in `backend/requirements-optional.txt`; `rapid-latex-ocr` does not install on Python 3.13+.

The exact files, sizes and SHA-256 hashes are recorded in `scripts/scanned_math_assets.manifest.json`. The repository records the
source model id (`breezedeus/pix2text-mfr`) but not the exact URL of the ONNX export these files came from, so the hashes are the
authoritative identity. Weights are git-ignored (`backend/model_weights/**/*.onnx`) and must never be committed or redistributed
without a separate licence/approval check.

## Install

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
# put the 8 model files in a folder (copy of the verified assets), then:
.venv\Scripts\python scripts\scanned_math_assets.py install --from <folder with the 8 files>
.venv\Scripts\python scripts\scanned_math_assets.py check
```

`install` refuses to copy files that do not match the manifest and never overwrites a different existing file without `--force`.
`check` verifies the hashes, confirms the pinned package versions, loads the recognizer and decodes a small test image.
Default location: `backend/model_weights/pix2text_mfr` (override with `AXTRACT_MFR_DIR`; the model root with `AXTRACT_MODEL_DIR`).
No file in another Git worktree is needed.

## Enable and tune

`AXTRACT_SCANNED_MATH=1` turns it on. If it is on but the packages or weights are missing, the app logs one warning naming the
missing pieces and extraction continues without scanned math. Per page: 20 s recognition budget (`AXTRACT_MFR_PAGE_SECONDS`),
24 lines (`AXTRACT_MFR_MAX_LINES`), 5 s per prediction, ONNX threads `AXTRACT_ORT_THREADS` (default 2).

## Known behaviour to expect

Recognition is CPU-only and time-capped, so equation-heavy pages score differently under machine load (same code, same page,
more or fewer lines finished inside the 20 s budget). Short typed lines and prose are never sent to the recognizer, and a
page-sized false table can no longer hide a page's equations from it.
