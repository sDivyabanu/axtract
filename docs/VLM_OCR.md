# VLM OCR engine (LightOnOCR-3-0.8B via llama.cpp)

An optional page-level engine for PDFs. A local vision-language model transcribes each page image into Markdown (LaTeX math,
HTML tables) together with a layout box and label for every block. It is **off by default**; with it off, AXTRACT behaves
exactly as before.

## Why this model
| | |
|---|---|
| Model | `lightonai/LightOnOCR-3-0.8B` (Qwen3.5 architecture, 0.9 B parameters) |
| Weights licence | Apache-2.0 (model card) |
| Runtime | llama.cpp `llama-server` (MIT), GGUF conversion `mradermacher/LightOnOCR-3-0.8B-GGUF` |
| Published olmOCR-Bench | 85.5 overall (LightOn's launch figures; not comparable to AXTRACT's 7-category macro) |
| Measured here | see "Results" below; frozen-70 diagnostic, unchanged official tests |

Larger systems (olmOCR-2-7B, Chandra, Infinity-Parser) need a CUDA GPU for usable speed. The development machine has none
(Ryzen 7 7840U, Radeon 780M iGPU, 16 GB RAM), while this model runs at roughly 30 tokens/s on the iGPU through Vulkan.

## How it plugs in
`AXTRACT_VLM_OCR` selects the pages it handles: `off` (default), `scanned` (pages the router classifies as scanned or
image-dominated), or `all`. For each selected page, `services/pdf_router.py` calls `services/vlm_engine.py`:
1. The page is rendered with pypdfium2, longest side 2048 px (the model's recommended input).
2. One request is sent with the model's `grounding` prompt, greedy decoding, max 4096 tokens.
3. The reply's blocks become DocumentBlocks: `extractor="lightonocr"`, `bbox` = the model's predicted box
   (`metadata.bbox_source="model_predicted"`; it is the model's estimate, not a coordinate read from the PDF), `vlm_label`,
   `model`, and the reading order the model produced.
   * page headers, footers and page numbers become HEADER/FOOTER blocks (kept in JSON, left out of the body Markdown);
   * HTML tables are parsed into rows plus explicit row/column spans and rendered by the standard table writer;
   * chart blocks keep the model's reading of the values but are flagged `chart_values_estimated` and marked for review;
   * a decoding loop is trimmed and the page's blocks are marked for review (`VLM_OUTPUT_TRUNCATED`).
4. If the server is unreachable, times out, or returns nothing, the page goes through the standard extractors and a
   `VLM_UNAVAILABLE` / `VLM_TIMEOUT` / `VLM_EMPTY` error is recorded. A dead server is not retried for 30 s.

Region readers (vector charts, scanned math) are not run on VLM pages, so nothing is added twice. Verification runs as before.
With the engine on, the per-file limit becomes `AXTRACT_VLM_DOC_SECONDS` (default 1800 s) instead of 60 s, because a page
takes tens of seconds on a laptop.

### Two models: fast general model, larger scan model
With `AXTRACT_VLM_SCAN_MODEL_ID` set, pages that are pictures of text (router route `scanned`/`mixed`, or covered >= 85 % by
images even when they carry a hidden OCR layer) go to a second server (`AXTRACT_VLM_SCAN_URL`), normally
`LightOnOCR-3-4B` (Apache-2.0). Born-digital pages stay on the 0.8B model. Measured on frozen-70 scan pages, the 4B model
raised old_scans 18 -> 27/54 and old_scans_math 95 -> 105/121 and was never worse on any page; on digital pages it brought
nothing (32/51 for both) at a seventh of the speed, so it is not used there. Every block records `model` and `vlm_profile`.

Settings: `AXTRACT_VLM_URL` (default `http://127.0.0.1:8090`), `AXTRACT_VLM_MODEL_ID` (recorded with every block and in
cache keys), `AXTRACT_VLM_LONGEST` (2048), `AXTRACT_VLM_MAX_TOKENS` (4096), `AXTRACT_VLM_PAGE_SECONDS` (600; raise it when the server batches several long pages),
`AXTRACT_VLM_CACHE_DIR` (optional reply cache; off by default because it holds document text unencrypted).

## Measured configurations (fixed 14-page subset, 92 assertions, unchanged scorer, uncached)
| Server config | Passed | Pages/hour (Radeon 780M, Vulkan) |
|---|---|---|
| 0.8B Q8_0, `-np 1` (reference) | 66/92 | 51.5 |
| 0.8B Q8_0, `-np 4`, 4 concurrent requests | 66/92 | 85.0 |
| 0.8B Q6_K, `-np 4` | 66/92 | 92.1 |
| 0.8B Q4_K_M, `-np 4` | 66/92 | 94.8 |
| 0.8B Q8_0, `-np 4`, 1540 px | 65/92 | 100.9 |
| 0.8B Q8_0, `-np 8` | 66/92 | 91.3 |
| 4B Q4_K_M, `-np 1` (scan pages only) | see above | ~34 on book scans, 8-12 on dense scans |
The 4B model with `-np 4` exhausts the 780M's Vulkan device memory; use `-np 1` there (a discrete GPU can batch it).

## Setup (Windows, once)
```powershell
# llama.cpp release b11719, Vulkan build (CPU build also works, slower)
curl -L -o llama-vulkan.zip https://github.com/ggml-org/llama.cpp/releases/download/b11719/llama-b11719-bin-win-vulkan-x64.zip
Expand-Archive llama-vulkan.zip llama-vulkan
# weights (sha256 recorded in axtract-vlm-assets/models/SHA256SUMS.txt)
curl -L -o lo3-0.8b-Q8_0.gguf https://huggingface.co/mradermacher/LightOnOCR-3-0.8B-GGUF/resolve/main/LightOnOCR-3-0.8B.Q8_0.gguf
curl -L -o lo3-0.8b-mmproj-f16.gguf https://huggingface.co/mradermacher/LightOnOCR-3-0.8B-GGUF/resolve/main/LightOnOCR-3-0.8B.mmproj-f16.gguf
```
| File | sha256 |
|---|---|
| lo3-0.8b-Q8_0.gguf | `b2955ffcce11b0c1fce6dd38d5303a0f3284161c48ade7f62bda9804ce5ba4f7` |
| lo3-0.8b-mmproj-f16.gguf | `c70a0eeee9bf2c2626510631a4c707321d48a91f577673304dd8771f2664f9d2` |

## Run
```powershell
llama-vulkan\llama-server.exe -m lo3-0.8b-Q8_0.gguf --mmproj lo3-0.8b-mmproj-f16.gguf --host 127.0.0.1 --port 8090 `
    -c 16384 -ngl 99 --jinja --reasoning off -np 1
$env:AXTRACT_VLM_OCR = "all"   # then start the backend as usual
```

## Benchmarks
```powershell
$data = "<olmOCR-bench>\bench_data"
# frozen 70-PDF diagnostic through the full AXTRACT pipeline
.venv\Scripts\python scripts\frozen70_benchmark.py extract --data-root $data --out runs\f70_vlm --scanned-math require --vlm all
.venv\Scripts\python scripts\frozen70_benchmark.py score --out runs\f70_vlm --scorer-python <py312> --scorer <olmocr checkout> --scorer-packages <site-packages>
.venv\Scripts\python scripts\frozen70_benchmark.py report --out runs\f70_vlm
# full 1,403-PDF benchmark: same flag on scripts\full_benchmark.py extract (resumable with --resume)
```
For large runs, fill the reply cache first, one model server at a time, then run the pipeline from the cache:
```powershell
$env:AXTRACT_VLM_CACHE_DIR = "D:\vlm-cache"; $env:AXTRACT_VLM_SCAN_MODEL_ID = "lo3-4b-Q4_K_M"; $env:AXTRACT_VLM_SCAN_URL = "http://127.0.0.1:8091"
# 4B server on :8091 (-np 1), then:
.venv\Scripts\python scripts\vlm_prefetch.py --data-root $data --profile scan --concurrency 1 --log-dir runs\prefetch_scan
# stop it, start the 0.8B server on :8090 (-np 4), then:
.venv\Scripts\python scripts\vlm_prefetch.py --data-root $data --profile general --concurrency 4 --log-dir runs\prefetch_general
.venv\Scripts\python scripts\full_benchmark.py extract --data-root $data --candidate-name axtract_vlm --scanned-math require --vlm all
```
The prefetch is resumable (a cached page is never re-sent), records failures in `failed.jsonl` instead of stopping, and
classifies pages with the same router code the pipeline uses, so the pipeline finds every reply it needs in the cache.

`--vlm` checks the server answers before the first document and raises the per-document hard limit to
`AXTRACT_VLM_HARD_SECONDS` (default 900 s). `scripts/vlm_frozen70.py` runs the model alone (no AXTRACT post-processing) and
`scripts/vlm_throughput.py` measures concurrency/quantisation settings; both cache raw replies keyed by PDF hash and request
settings. `scripts/vlm_seed_cache.py` loads such replies into the engine cache so a pipeline run can reuse them.
