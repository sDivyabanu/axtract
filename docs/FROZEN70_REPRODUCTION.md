# Reproducing the frozen 70-PDF diagnostic from a fresh clone

This reproduces AXTRACT's **diagnostic sample**: 70 PDFs and 430 official assertions chosen deterministically from olmOCR-bench
(manifest: `evaluation/frozen70/manifest.json`). It is **not** the full 1,403-PDF benchmark and not an unbiased accuracy estimate.
The official scorer is used unchanged. Tested on Windows 11 x64, Python 3.14.6 (AXTRACT) and 3.12.15 (scorer).

## 0. One-time provisioning (needs a network once; everything after runs offline)

| Input | Exact requirement | How |
|---|---|---|
| AXTRACT packages | `backend/requirements.txt` (pinned); `backend/requirements.lock.txt` is the full lock of the tested environment | `pip install` |
| Pix2Text-MFR weights | 8 files, 118 MB, MIT licence, SHA-256 in `scripts/scanned_math_assets.manifest.json` | **you supply the files**; `scripts/scanned_math_assets.py install --from <folder>` verifies and installs them. The tool never downloads. Source model id: `breezedeus/pix2text-mfr`. |
| olmOCR-bench data | dataset revision `54a96a6fb6a2bd3b297e59869491db4d3625b711` (`allenai/olmOCR-bench`), folder `bench_data` | the harness verifies every PDF and assertion file by SHA-256 and stops on any difference |
| Official scorer | `https://github.com/allenai/olmocr` at commit `f7cfe4c22098b154c76b6ec950d1c0a464eecf8d` (`olmocr/bench/tests.py` sha256 `d39d4baa…`) | `git clone`, then `git checkout f7cfe4c2…` |
| Scorer interpreter | Python 3.12 with `evaluation/frozen70/scorer-requirements.txt`, then `python -m playwright install chromium` once | the scorer renders LaTeX in a browser |

## 1. Install AXTRACT in a clean virtual environment

```powershell
git clone <repository> axtract; cd axtract; git checkout feature/scanned-accuracy-sprint
py -3.14 -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt
.venv\Scripts\python scripts\scanned_math_assets.py install --from <folder with the 8 Pix2Text-MFR files>
.venv\Scripts\python scripts\scanned_math_assets.py check
```

`check` must print `OK: Pix2Text-MFR loads from ...`. With nothing overridden, the weights live in
`backend\model_weights\pix2text_mfr` (git-ignored). No file outside the clone is used.

## 2. Run the diagnostic

```powershell
$data = "<...>\olmOCR-bench\bench_data"
.venv\Scripts\python scripts\frozen70_benchmark.py extract --data-root $data --out runs\frozen70_on --scanned-math require
.venv\Scripts\python scripts\frozen70_benchmark.py score   --data-root $data --out runs\frozen70_on `
    --scorer-python <py312>\python.exe --scorer <olmocr checkout>
.venv\Scripts\python scripts\frozen70_benchmark.py report  --out runs\frozen70_on --scanned-math require
```

Use `--scanned-math off` (and `report --scanned-math off`) for the default configuration. Extraction takes about 15-25 minutes
on a quiet 16-thread machine, one document at a time, 75 s hard limit per document, waiting for free RAM instead of failing.

`--scanned-math require` makes a missing package, missing weight or hash mismatch a **hard error before the first document**
(`AXTRACT_SCANNED_MATH=require`); plain `1` only logs a warning and continues without scanned math.

## 3. What to expect

`report` prints the seven category scores next to the reference ranges in the manifest and ends with
`RESULT: MATCHES the frozen reference` or `DIFFERS`. Scanned math is time-capped, so `old_scans_math` varies with machine load:
the reference is a range, not a single number. Every other category must match exactly.

## Offline operation

After section 0, nothing in extraction or scoring needs the network: model files are read from disk, no package or model is
fetched at runtime, and the harness removes `AXTRACT_MFR_DIR`, `AXTRACT_FORMULA_SITE` and `AXTRACT_MODEL_DIR` from the
environment so a reproduction cannot silently depend on files elsewhere.
