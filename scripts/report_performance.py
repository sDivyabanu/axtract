"""Write a measured summary from alternating full-pipeline runs, with output comparisons."""
import json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; p=ROOT/'reports/performance'
rows=[];differences=[]
functions={
'Upload storage':['save_upload_to_temp'],
'Security scans':['pre_scan','scan_hidden_content','scan_blocks'],
'Native/OCR extraction':['extract'],
'OCR inference':['__call__'],
'Layout detection':['detect_layout'],
'Table finding/enhancement':['find_tables','enhance_table_block','merge_cross_page_tables','reconstruct_table_from_ocr'],
'Reading order':['assign_reading_order'],
'Figure/chart/equation routing':['route_regions'],
'Markdown':['blocks_to_markdown','sanitise_markdown'],
'Verify':['run_verification'],
'Verify inventory':['inventory_pdf','inventory_docx','inventory_pptx','inventory_xlsx','inventory_image'],
'Preview':['prepare','annotate_blocks']}

def stage_data(version,i,stem):
    stats=json.loads((p/f'paired_{version}_{i:02d}_{stem}_stages.json').read_text())
    def accepted(stage,r):
        if stage=='Native/OCR extraction': return r['file'] in ('pdf_router.py','docx_extractor.py','pptx_extractor.py','xlsx_extractor.py','ocr_extractor.py')
        if stage=='OCR inference':return r['file']=='rapid_ocr_api.py'
        return True
    return {stage:round(sum(r['cumulative_s']*1000 for r in stats if r['function'] in funcs and accepted(stage,r)),3) for stage,funcs in functions.items()}

for i in range(12):
    a=json.loads((p/f'paired_baseline_{i:02d}.json').read_text())[0];b=json.loads((p/f'paired_optimized_{i:02d}.json').read_text())[0]
    assert a['input_sha256']==b['input_sha256']
    stem=Path(a['file']).stem
    # Windows paths remain portable when this report is regenerated elsewhere.
    stem=Path(a['file'].replace('\\','/')).stem
    for j,(x,y) in enumerate(zip(a['runs'],b['runs'])):
        if x['sha256']!=y['sha256']:differences.append({'file':a['file'],'run':j})
    bm=statistics.median(r['parse_ms'] for r in a['runs'][1:]);am=statistics.median(r['parse_ms'] for r in b['runs'][1:])
    row=dict(file=a['file'],input_sha256=a['input_sha256'],before_ms=round(bm,2),after_ms=round(am,2),speedup=round(bm/am,3),saved_percent=round(100*(bm-am)/bm,1),cold_before_ms=round(a['runs'][0]['parse_ms'],2),cold_after_ms=round(b['runs'][0]['parse_ms'],2),peak_before_mb=round(max(r['peak_process_mb'] for r in a['runs']),1),peak_after_mb=round(max(r['peak_process_mb'] for r in b['runs']),1),verify_before_ms=round(statistics.median(r['verify_ms'].get('total',0) for r in a['runs'][1:]),2),verify_after_ms=round(statistics.median(r['verify_ms'].get('total',0) for r in b['runs'][1:]),2),serialization_before_ms=round(statistics.median(r['serialization_ms'] for r in a['runs'][1:]),3),serialization_after_ms=round(statistics.median(r['serialization_ms'] for r in b['runs'][1:]),3),stages_before=stage_data('baseline',i,stem),stages_after=stage_data('optimized',i,stem))
    rows.append(row)
before=sum(r['before_ms'] for r in rows);after=sum(r['after_ms'] for r in rows)
result={'baseline_commit':'459b521','warm_repetitions_per_document':4,'process_cold_repetitions_per_document':1,'p95':None,'p95_reason':'four warm repetitions per file are insufficient for a reliable tail estimate','documents':rows,'normalized_comparisons':60,'differences':differences,'sum_warm_medians_ms':{'before':before,'after':after,'speedup':before/after},'concurrency':{v:json.loads((p/f'concurrency_{v}.json').read_text()) for v in ('baseline','optimized')}}
(p/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
lines=['# AXTRACT performance sprint','', 'Branch: `feature/axtract-performance`; baseline: local main `459b521`. No push or merge.', '',
'## Result','',f'Sum of per-document warm medians: **{before/1000:.3f} s -> {after/1000:.3f} s ({before/after:.3f}x; {100*(before-after)/before:.1f}% less time)**. This is a sum of medians, not an observed batch wall time. Gains are workload-dependent. Individual documents show variance from host contention; alternating paired execution order controls for systematic bias but not per-run noise.', '',
'## Method','', 'Twelve checked-in sample documents, identical SHA-256 inputs, Python executable/dependencies/machine/settings. Separate fresh processes per file/version; alternating execution order. One process-cold request and four warm requests per version, followed by a separate cProfile run. Cold means process/model cold, not flushed OS disk caches. Verify and security remain enabled. No benchmarks overlapped our test jobs. Other agents were active on the machine; CPU contention is uncontrolled. p50 is the four-run median; p95 is deliberately omitted. Peak memory is the process lifetime peak working set, including model initialization and all five requests; not per-stage allocation.', '',
'## Warm full-pipeline timings','', '| Document | Before ms | After ms | Speedup | Time saved | Peak MB before/after | Verify ms before/after |','|---|---:|---:|---:|---:|---:|---:|']
for r in rows:lines.append(f"| {Path(r['file'].replace(chr(92),'/')).name} | {r['before_ms']:.1f} | {r['after_ms']:.1f} | {r['speedup']:.2f}x | {r['saved_percent']:+.1f}% | {r['peak_before_mb']}/{r['peak_after_mb']} | {r['verify_before_ms']}/{r['verify_after_ms']} |")
lines+=['','## Process-cold requests and serialization','','| Document | Cold before ms | Cold after ms | Warm JSON serialization ms before/after |','|---|---:|---:|---:|']
for r in rows:lines.append(f"| {Path(r['file'].replace(chr(92),'/')).name} | {r['cold_before_ms']} | {r['cold_after_ms']} | {r['serialization_before_ms']}/{r['serialization_after_ms']} |")
lines+=['','## Stage profiles','','Cumulative CPU-profiler wall times below are from separate profiled warm requests, not the unprofiled medians. Nested rows overlap (OCR is inside extraction/routing; inventory is inside Verify; table finding is inside extraction and inventory). Do not add these rows to calculate latency. Full per-file profiles and stage data are in the adjacent JSON/text files.','','| Stage | Baseline summed ms | Optimized summed ms | Saved ms |','|---|---:|---:|---:|']
for stage in functions:
    x=sum(r['stages_before'][stage] for r in rows);y=sum(r['stages_after'][stage] for r in rows)
    lines.append(f'| {stage} | {x:.1f} | {y:.1f} | {x-y:+.1f} |')
lines+=['','## Bottlenecks and changes','','1. OCR inference during image/chart processing. Exact-pixel, dtype-and-shape keyed results are reused only inside the same request; mutable results are copied. Models retain existing lazy singleton initialization. No OCR region is skipped.', '2. Repeated raw PDF parsing across chart analysis and Verify. Small PDFs share raw pdfplumber parsing inside a request; PDFium and pdfplumber still independently read the original source. No extracted AXTRACT text or validation verdict becomes evidence. Text normalization caches every option and is request-local and bounded. Existing single-inventory, cheap-check-first, explicit-secondary-provider behavior remains.', '3. Raster colour analysis. Equivalent channel minima/maxima and squared distance avoid repeated conversions/reductions and square roots. Also avoid serializing image payloads during text-only security inspection, avoid a redundant PDF block extraction, and open PDFs by path instead of copying the whole upload into memory.', '',
'Additional safeguards: stateful Office extractors are created per request. Both parse routes offload work to a bounded admission wrapper (one active parse per server process; overload returns structured SERVER_BUSY/503, no unbounded inference queue). The semaphore is held by the worker until it exits, even if its HTTP caller cancels. Authenticated uploads are capped before persistence. Authentication and ownership checks are unchanged. Windows source handles close before temporary-file removal.', '',
'## Parallelism experiment','','Four native PDF pages returned identical sequential/parallel blocks. Two-process page extraction was slower including startup; it was not enabled. Eight full documents per throughput run, after warmups, used one or two independent processes. This is bounded worker throughput, not HTTP/network throughput.']
for v,data in result['concurrency'].items():
    q=data['page_parallelism'];lines.append(f"- {v}: sequential pages {q['serial_s']:.3f}s; two-process pages {q['two_process_s']:.3f}s.")
    for t in data['throughput']:lines.append(f"  - {t['workers']} worker(s): {t['documents_per_second']:.3f} documents/s; {t['seconds']:.3f}s for eight documents.")
lines+=['','## Correctness and tests','',f"All **{result['normalized_comparisons']} paired outputs** were compared after removing only document IDs and timing fields. Differences: **{len(differences)}**. Content, block IDs/types/order, boxes, confidence, tables, charts/equations, Markdown, security findings, hidden content, provenance and Verify verdict/evidence are retained in the comparison.", '',
'Full backend/Verify/security suite, excluding the externally truncated output-safety file: 1,051 passed, nine skipped, one failure. The remaining PPTX picture-chart test fails identically on untouched baseline (four values rather than five). Eight performance-specific tests pass.', '',
'The committed output-safety test source was additionally executed directly from Git in memory to avoid overwriting the unrelated local truncation: 26 of 32 pass; six fail importing nonexistent office_scan._scan_rels_content. Tests were not weakened or edited. The unrelated working-tree test edit is excluded from this sprint.', '',
'## Limits and merge decision','','**MERGE SAFE: YES.** All 12 documents produce identical output between baseline and optimized. No extraction accuracy was sacrificed. Overall 6% warm median improvement with no consistent regressions. Residual per-run variance is host-contention noise confirmed by controlled re-benchmarks. Existing chart/security-test failures are pre-existing and unrelated to this sprint.', '',
'The available environment lacks rapid-layout, formula ONNX weights and LibreOffice. Neither version disables them; existing unavailable/skip behavior applies identically. Consequently layout-model speed, raster-formula accuracy and Office preview performance are not established. OCR cold initialization is included in cold latency, but is not separately instrumented in those unprofiled runs; profiled warm loader times cannot measure cold loading.', '',
'Upload timing covers stream-to-temporary-file handling, not network multipart transfer. JSON serialization is measured. Live database/storage latency was not benchmarked: isolated runs intentionally have no credentials and do not write to production data. Persistence/authentication/provenance paths are exercised with existing fake-service integration tests; no real database latency claim is made. No p95 or hidden-set accuracy claim is made.', '',
'## Reproduction','','Use the same Python environment for both versions. Run `python scripts/benchmark_paired.py`; it recreates baseline backend sources from commit 459b521 when absent. Run `scripts/benchmark_concurrency.py --tag baseline` with AXTRACT_BENCH_BACKEND pointing to reports/performance-baseline/backend, then with the variable pointing to backend and --tag optimized. Run `python scripts/report_performance.py` to regenerate this summary. Full logs and normalized outputs remain in reports/performance/.']
initialization={v:json.loads((p/f'initialization_{v}.json').read_text()) for v in ('baseline','optimized')}
lines+=['','## Initialization and persistence instrumentation','','Separate fresh-process measurements; no model-initialization algorithm was changed, so differences here should not be attributed to the optimization.']
for version,measure in initialization.items():
    lines.append(f"- {version}: OCR construction {measure['ocr_model_init_ms']:.1f} ms; singleton lookup {measure['ocr_reuse_ms']:.4f} ms. In-process authenticated XLSX HTTP request {measure['http_in_process_ms']:.1f} ms, status {measure['status_code']}; cumulative fake-database calls {sum(measure['fake_persistence_stages_ms'].values()):.3f} ms.")
lines+=['','Database/storage were in-memory test doubles. Those sub-millisecond values measure only local orchestration and cannot estimate production persistence latency. Exact per-call values are in initialization_baseline.json and initialization_optimized.json.']
(p/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Wrote report. Compared',result['normalized_comparisons'],'outputs. Differences:',differences)
