# Full olmOCR-Bench run: laptop GPU + AWS CPU in parallel

Goal: the shortest wall-clock time for the 0.8B + 4B hybrid, with nothing about extraction or scoring weakened.

## Who does what
| Machine | Work | Why |
|---|---|---|
| Laptop (Radeon 780M, Vulkan) | Model inference only: fill the reply cache with `scripts/vlm_prefetch.py` (4B for the 290 scan-like pages, 0.8B batched 4 for the 1,113 others) | The only GPU; decode is 81 % of page time |
| AWS r7i.xlarge (4 vCPU, 32 GiB, CPU) | AXTRACT pipeline from the cache (`full_benchmark.py extract --vlm all --resume`), fast scoring (`fast_score.py --jobs 4`), final official CLI scoring | CPU work; 32 GiB lifts the 2 GiB caps that slowed earlier CLI scoring |

The cache is the hand-off: the laptop writes `AXTRACT_VLM_CACHE_DIR/<2 hex>/<sha256>.json` files (atomic, never rewritten);
copying new files to the VM is safe at any time (for example `scp -r` or `rsync` every 30-60 min, or `aws s3 sync`).
The pipeline on the VM only needs the cache; it never needs a model server for a cached page. An uncached page is not
skipped: it falls back to AXTRACT's standard extractors and is recorded as `VLM_UNAVAILABLE`, so run the VM's final
extraction pass only after the laptop's prefetch has finished (or re-run those documents with `--resume` after deleting them).

## Order on the laptop (one model server at a time)
1. 4B scan pages first (longest pole, ~17 h measured average 211 s/page), then the 0.8B pages (~13 h at 85 pages/h).
   Starting the 0.8B pages first instead lets the VM begin the pipeline/scoring of digital categories earlier; total
   inference time is the same.
2. Both prefetch runs are resumable: re-running the same command skips every cached page.

## Order on the VM
1. As cache chunks arrive: nothing to do until a category is complete (optional: run the pipeline per category with
   `--category`).
2. After the laptop finishes: `full_benchmark.py extract --vlm all` (about 1 h single process; `--resume` safe).
3. `fast_score.py --jobs 4` (estimated 15-25 min for 7,010 assertions) for the immediate number.
4. Official CLI scoring (`full_benchmark.py score`) for the headline; with 32 GiB it should be well under the earlier
   2h43m but is not measured on the VM.

## Wall-clock estimate (realistic; see VLM_RUNTIME_ETA.md for ranges)
Laptop inference ~30 h, then VM pipeline ~1 h + fast score ~0.3 h, official CLI in parallel/after. Total ~31-32 h.
Without the VM, the laptop would add the pipeline and scoring tail (~1.5-3 h) to the end.

## Commands (VM, Linux)
```bash
export AXTRACT_VLM_CACHE_DIR=$HOME/vlm-cache AXTRACT_VLM_SCAN_MODEL_ID=lo3-4b-Q4_K_M AXTRACT_VLM_MODEL_ID=LightOnOCR-3-0.8B-Q8_0
# the pipeline checks a server answers before the first document; on the VM point it at any llama-server
# (a CPU llama-server with the 0.8B model is enough: cached pages never reach it)
python scripts/full_benchmark.py extract --data-root $DATA --candidate-name axtract_vlm_hybrid --scanned-math require --vlm all
python scripts/fast_score.py --data-root $DATA --candidate $DATA/axtract_vlm_hybrid --out runs/fast_score_full \
    --scorer-python $PY312 --scorer $OLMOCR --scorer-packages $SITE --jobs 4
```
The model IDs must be exactly the strings used on the laptop, because they are part of every cache key.
