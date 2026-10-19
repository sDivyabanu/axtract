# Full olmOCR-Bench runtime prediction (1,403 PDFs, 7,010 assertions)

Measured on 2026-10-19 on the Ryzen 7 7840U / Radeon 780M laptop unless marked *theoretical*.

## Measured inputs
| Measurement | Value | Source |
|---|---|---|
| Native AXTRACT, full run | extraction 5,714 s (1h35m), scoring 9,785 s (2h43m) | `full_official_20261018_latest_01/REPORT.md` |
| 0.8B Q8, 4 batched, 14 mixed pages (all 7 categories) | 85.0 pages/h (1.42 pages/min) | `sub14_e1_q8_np4_2048` |
| 0.8B Q4_K_M, 4 batched, same pages | 94.8 pages/h, same 66/92 score | `sub14_e2_q4km_np4_2048` |
| 0.8B Q8, 1 at a time, frozen-70 layout mode | 70 pages / 5,107 s = 49 pages/h | `f70_lo3_ground` |
| 4B Q4_K_M, 1 at a time, 34 scan-like frozen-70 pages | 211 s/page avg (54-560 s) = 17 pages/h | `prefetch_f70_scan` + `scans_4b_q4km_np1` |
| Page time split (0.8B, 70 pages) | decode 81 %, image prefill 16 %, render/HTTP/other ~1-2 % | llama-server timings |
| AXTRACT pipeline on cached replies (verify, tables, Markdown, one process per PDF) | 2.7 s/PDF (187 s / 70) | `f70_axtract_vlm_cached_v2` |
| Scoring, per-assertion official tests (`load_single_test(case).run`), 430 assertions | 73 s sequential, 56 s 7-way parallel | `score_timing_seq/par` (359/430 in both = identical) |
| CPU-only proxy for r7i.xlarge (llama.cpp CPU build, 4 threads on the 7840U, machine busy) | 216 s and 338 s per page (prefill 18 tok/s, decode 6.5-7 tok/s) | `cpu4_proxy` |
| Scan-like pages in the full benchmark (AXTRACT's router + image coverage) | 290 of 1,403 | computed |

## Throughput needed
Extraction must leave room for the pipeline pass (~1 h sequential, ~20 min with 4 workers) and scoring (~25 min).
| Finish in | Extraction budget | Pages/min needed |
|---|---|---|
| 3 h | ~2.2 h | 10.6 |
| 4 h | ~3.2 h | 7.3 |
| 6 h | ~5.2 h | 4.5 |
Measured on the laptop: 0.8B 1.42 pages/min; 0.8B+4B hybrid ~0.75 pages/min effective.

## Prediction table
| Machine | Pipeline | Measured pages/min | Predicted extraction | Predicted scoring | Total optimistic / realistic / pessimistic | Accuracy evidence | Confidence |
|---|---|---|---|---|---|---|---|
| Laptop 780M | Native AXTRACT (no VLM) | ~14.7 (measured full run) | 1h35m | 25 min (per-assertion workers) / 2h43m (official CLI batches) | 2.0 / 2.2 / 4.3 h | **50.09 % official, full** | High (measured) |
| Laptop 780M | 0.8B hybrid (all pages 0.8B, batched 4) | 1.42 (Q8) - 1.58 (Q4_K_M) | 14.8-16.5 h + 1 h pipeline | 25 min | 16 / 18 / 30 h | frozen-70 75.27 % (diagnostic) | Medium-high (measured rate, mix extrapolated) |
| Laptop 780M | 0.8B + 4B hybrid | 1.42 digital, 0.28 scans | 13.1 h digital + 17 h scans + 1 h | 25 min | 24 / 31 / 38 h | frozen-70 82.52 % (diagnostic, cached replies; Set B pending) | Medium |
| AWS r7i.xlarge (4 vCPU, CPU) | Native AXTRACT | ~12-15 *(theoretical)* | 1.5-2 h | 25-40 min | 2 / 2.5 / 3.5 h | 50.09 % | Medium (same code, similar cores) |
| AWS r7i.xlarge | 0.8B hybrid | ~0.2 *(proxy)* | ~108 h (AMX might cut this 2-3x: 40-55 h) | 25-40 min | 40 / 80 / 120 h | as above | Low (proxy measurement) |
| AWS r7i.xlarge | 0.8B + 4B hybrid | < 0.2 *(proxy)* | > 150 h | — | not viable | as above | Low |
| NVIDIA L4 (e.g. g6.xlarge) | 0.8B + 4B hybrid | *theoretical* ~15-25 digital, 3-5 scans | 1.5-3 h | 25 min | 2.5 / 3.5 / 6 h | as above | Low (not measured; needs a 30-page calibration first) |
| NVIDIA L4 | 0.8B hybrid | *theoretical* ~15-25 | 1-1.5 h | 25 min | 1.8 / 2.5 / 4 h | as above | Low |

L4 assumptions: llama.cpp CUDA build, 0.8B with `-np 8-16`, 4B with `-np 4-8`; the L4 has ~300 GB/s memory bandwidth and 24 GB VRAM versus the 780M's shared system memory, and batching scales far better on it. These are not measurements.

## Bottleneck and settings
* Bottleneck: VLM token decoding (81 % of page time). Rendering, HTTP, model load (9 s once) and AXTRACT post-processing are minor.
* Laptop: 0.8B server `-np 4` (peak ~2.6-3.5 GB RAM); 4B server `-np 1` (peak ~5.9 GB; `-np 4` exhausts Vulkan device memory). Run the two prefetch phases one after the other.
* Scoring: the per-assertion runner (`frozen70_benchmark.py score-worker`, the official test classes unchanged) takes ~25 min for 7,010 assertions versus 2h43m for the batched official CLI; the official CLI remains the source of the headline number.
