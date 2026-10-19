"""Isolated Azure OCR diagnostic: fixed 14/70 pages, bounded processes and USD reservations.

Does not modify local/VLM benchmark runners or their deadlines. No automatic full-corpus run.
Run --help for extraction and optional pinned official assertion scoring.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'scripts')]


def selection(data, scope, dataset='A'):
    import frozen70_benchmark as frozen
    if dataset == 'B':
        # Set B: every PDF under the supplied data root (a second fixed 70-PDF sample, not the frozen manifest).
        wanted = sorted(p.relative_to(data/'pdfs').as_posix() for p in (data/'pdfs').glob('*/*.pdf'))
        wanted_set = set(wanted)
        cases = [json.loads(line) for f in sorted(data.glob('*.jsonl'))
                 for line in f.read_text(encoding='utf8').splitlines() if line.strip()]
        cases = [c for c in cases if c['pdf'] in wanted_set and c.get('type') != 'baseline']
        return wanted, cases
    manifest = frozen.manifest()
    wanted = list(manifest['pdfs'])
    if scope == '14':
        counts = {}
        picked = []
        for pdf in wanted:
            category = pdf.split('/')[0]
            if counts.get(category, 0) < 2:
                picked.append(pdf)
                counts[category] = counts.get(category, 0) + 1
        wanted = picked
    for name, digest in manifest['dataset']['case_source_sha256'].items():
        if frozen.sha256(data/name) != digest:
            raise SystemExit('Assertion dataset does not match the frozen manifest: '+name)
    for pdf in wanted:
        if frozen.sha256(data/'pdfs'/pdf) != manifest['pdfs'][pdf]:
            raise SystemExit('PDF does not match frozen manifest: '+pdf)
    wanted_set = set(wanted)
    cases = [json.loads(line) for f in sorted(data.glob('*.jsonl'))
             for line in f.read_text(encoding='utf8').splitlines() if line.strip()]
    cases = [c for c in cases if c['pdf'] in wanted_set and c.get('type') != 'baseline']
    return wanted, cases


def worker(args):
    # One fresh process per document: no cross-document caches or deadline/model state.
    from benchmark_axtract_olmocr import process_single_pdf
    from table_accuracy_sample import process_peak_working_set
    result = process_single_pdf(args.data_root/'pdfs'/args.pdf, Path(args.pdf), args.out/'candidate', True)
    result['peak_working_set_bytes'] = process_peak_working_set()
    args.result.write_text(json.dumps(result, indent=2), encoding='utf8')
    return 0


def run(args):
    from dotenv import load_dotenv
    load_dotenv(ROOT/'backend/.env', override=False)
    from extractors.azure_ocr_options import configuration, public_profile
    os.environ['AXTRACT_OCR_PROVIDER'] = 'azure'
    os.environ['AXTRACT_VLM_OCR'] = 'off'
    # A reservation cap covers all requests/retries for one benchmark page.
    os.environ['AXTRACT_AZURE_MAX_USD_PER_PAGE'] = str(args.page_cap_usd)
    config = configuration()
    if args.max_usd < args.workers*args.page_cap_usd:
        raise SystemExit('Budget is smaller than one wave of document reservations.')
    if bool(args.scorer_python) != bool(args.scorer):
        raise SystemExit('Supply both --scorer-python and --scorer to score this run.')
    if args.scorer:
        import frozen70_benchmark as frozen
        if frozen.sha256(args.scorer/'olmocr/bench/tests.py') != frozen.manifest()['scorer']['tests_py_sha256']:
            raise SystemExit('Scorer does not match frozen manifest.')
    verify_scorer(args)
    pdfs, cases = selection(args.data_root, args.scope, getattr(args, 'set', 'A'))
    import pymupdf
    for pdf in pdfs:
        with pymupdf.open(args.data_root/'pdfs'/pdf) as doc:
            if len(doc) != 1:
                raise SystemExit('This diagnostic requires one-page benchmark PDFs.')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out/'candidate').mkdir()
    (args.out/'docs').mkdir()
    (args.out/'selected_original_cases.json').write_text(json.dumps(cases), encoding='utf8')
    profile = dict(scope='fixed '+args.scope+'-page diagnostic (set '+getattr(args, 'set', 'A')+'); not full corpus', dataset=getattr(args, 'set', 'A'),
        pdf_sha256={pdf: hashlib.sha256((args.data_root/'pdfs'/pdf).read_bytes()).hexdigest() for pdf in pdfs}, max_usd=args.max_usd,
        page_reservation_usd=args.page_cap_usd, workers=args.workers, azure=public_profile(config),
        code_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
            ['backend/extractors/azure_ocr_extractor.py','backend/extractors/azure_ocr_options.py','scripts/azure_benchmark.py']})
    (args.out/'azure_profile.json').write_text(json.dumps(profile, indent=2), encoding='utf8')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', AXTRACT_VERIFY='1')
    started = time.perf_counter()
    docs = []
    charged = 0.
    print(f'{len(pdfs)} fixed PDFs; {len(cases)} assertions; deployment {config["deployment"]}; USD allowance {args.max_usd}', flush=True)
    # Batches reserve costs before dispatch. Receipts replace reservations when available.
    for offset in range(0, len(pdfs), args.workers):
        wave = pdfs[offset:offset+args.workers]
        if charged + len(wave)*args.page_cap_usd > args.max_usd:
            print('Budget reservation limit reached; remaining PDFs are unattempted.', flush=True)
            for pdf in pdfs[offset:]:
                docs.append(dict(pdf=pdf, success=False, error='CLOUD_RUN_BUDGET_LIMIT', reserved_or_reported_usd=0))
            break
        def one(pdf):
            index = pdfs.index(pdf)
            result = args.out/'docs'/f'{index:03}.json'
            log = args.out/'docs'/f'{index:03}.log'
            cmd = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), 'worker',
                   '--data-root', str(args.data_root), '--out', str(args.out), '--pdf', pdf, '--result', str(result)]
            t0 = time.perf_counter()
            with log.open('w', encoding='utf8') as stream:
                proc = subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=stream, stderr=stream)
                try:
                    proc.wait(timeout=config['doc_seconds']+30)
                except subprocess.TimeoutExpired:
                    import frozen70_benchmark as frozen
                    frozen.kill_tree(proc); proc.wait()
            if result.exists():
                row = json.loads(result.read_text(encoding='utf8'))
            else:
                row = dict(pdf=pdf, success=False, error='Worker timeout/crash; entire page reservation retained',
                           reserved_or_reported_usd=args.page_cap_usd)
            row['wall_seconds'] = round(time.perf_counter()-t0, 3)
            return row
        with ThreadPoolExecutor(args.workers) as pool:
            for future in as_completed([pool.submit(one, pdf) for pdf in wave]):
                row = future.result(); docs.append(row)
                charged += row.get('reserved_or_reported_usd', args.page_cap_usd)
                with (args.out/'documents.jsonl').open('a', encoding='utf8') as stream:
                    stream.write(json.dumps(row)+'\n')
                print(f'{len(docs)}/{len(pdfs)} {row["pdf"]}: success={row["success"]} {row["wall_seconds"]}s reserved/reported=${charged:.4f}', flush=True)
    performance = dict(documents=docs, wall_seconds=round(time.perf_counter()-started, 2),
                       reserved_or_reported_usd=round(charged, 6), successful=sum(d['success'] for d in docs),
                       native_reused_pages=sum(d.get('native_reused_pages',0) for d in docs),
                       usage={key:sum(event.get('usage',{}).get(key,0) for d in docs for page in d.get('cloud_requests',[]) for event in page['requests']) for key in ['input_tokens','output_tokens','total_tokens']})
    (args.out/'performance.json').write_text(json.dumps(performance, indent=2), encoding='utf8')
    if args.scorer:
        return score_run(args, performance, profile)
    return 0


def verify_scorer(args):
    if bool(args.scorer_python) != bool(args.scorer):
        raise SystemExit('Supply both --scorer-python and --scorer.')
    if not args.scorer:
        return
    import frozen70_benchmark as frozen
    if not args.scorer_python.is_file():
        raise SystemExit('Scorer interpreter not found: '+str(args.scorer_python))
    check = subprocess.run([str(args.scorer_python), '--version'], capture_output=True, timeout=15)
    if check.returncode:
        raise SystemExit('Scorer interpreter could not start. Fix it before a paid run.')
    if frozen.sha256(args.scorer/'olmocr/bench/tests.py') != frozen.manifest()['scorer']['tests_py_sha256']:
        raise SystemExit('Scorer does not match the pinned manifest.')


def score_run(args, performance=None, profile=None):
    verify_scorer(args)
    if any(args.out.glob('score_*.jsonl')):
        raise SystemExit('Score ledgers already exist; preserve this run and use a fresh scoring copy.')
    performance = performance or json.loads((args.out/'performance.json').read_text(encoding='utf8'))
    profile = profile or json.loads((args.out/'azure_profile.json').read_text(encoding='utf8'))
    cases = json.loads((args.out/'selected_original_cases.json').read_text(encoding='utf8'))
    score_start = time.perf_counter()
    import frozen70_benchmark as frozen
    for category in sorted({c['pdf'].split('/')[0] for c in cases}):
        cmd = [str(args.scorer_python), '-B', '-X', 'utf8', str(ROOT/'scripts/frozen70_benchmark.py'),
               'score-worker', str(args.out), category, '--scorer', str(args.scorer)]
        if args.scorer_packages:
            cmd += ['--packages', str(args.scorer_packages)]
        scored = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf8', timeout=600)
        (args.out/f'score_{category}.log').write_text(scored.stdout+'\n'+scored.stderr, encoding='utf8')
        if scored.returncode:
            raise SystemExit('Official scoring failed: '+category)
        print(category+': scoring complete', flush=True)
    rows = [json.loads(s) for f in args.out.glob('score_*.jsonl') for s in f.read_text(encoding='utf8').splitlines() if s.strip()]
    if len(rows) != len(cases):
        raise SystemExit('Assertion count mismatch')
    cats = {}
    for row in rows:
        value = cats.setdefault(row['pdf'].split('/')[0], dict(passed=0, total=0))
        value['total'] += 1; value['passed'] += row['passed'] is True
    for value in cats.values():
        value['percentage'] = round(100*value['passed']/value['total'], 2)
    summary = dict(scope=profile['scope'], categories=cats, passed=sum(r['passed'] is True for r in rows), total=len(rows),
        macro_percentage=round(sum(v['passed']/v['total'] for v in cats.values())/len(cats)*100, 2),
        scorer_exceptions=sum(bool(r.get('scorer_error')) for r in rows), score_seconds=round(time.perf_counter()-score_start, 2),
        extraction={k:v for k,v in performance.items() if k!='documents'})
    (args.out/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf8')
    print(json.dumps(summary, indent=2), flush=True)
    return 0


def recover(args):
    """A generic second stage for failed pages, never selected by assertion outcomes."""
    from dotenv import load_dotenv
    load_dotenv(ROOT/'backend/.env', override=False)
    from extractors.azure_ocr_options import configuration, public_profile
    verify_scorer(args)
    base_profile = json.loads((args.source/'azure_profile.json').read_text(encoding='utf8'))
    base = json.loads((args.source/'performance.json').read_text(encoding='utf8'))
    for name in ['backend/extractors/azure_ocr_extractor.py','backend/extractors/azure_ocr_options.py']:
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != base_profile['code_sha256'][name]:
            raise SystemExit('Extraction code differs from first pass: '+name)
    os.environ.update(AXTRACT_OCR_PROVIDER='azure', AXTRACT_VLM_OCR='off',
        AXTRACT_AZURE_TIMEOUT_SECONDS='150', AXTRACT_AZURE_DOCUMENT_SECONDS='240',
        AXTRACT_AZURE_MAX_OUTPUT_TOKENS='8192', AXTRACT_AZURE_MAX_USD_PER_PAGE=str(args.page_cap_usd),
        AXTRACT_AZURE_MAX_RETRIES='0', AXTRACT_AZURE_MAX_REQUESTS_PER_PAGE='1', AXTRACT_AZURE_QUALITY_REREADS='0')
    config = configuration()
    if config['deployment'] != base_profile['azure']['deployment']:
        raise SystemExit('Recovery deployment differs from first pass.')
    # Validate immutable benchmark inputs before reusing any first-pass artifacts.
    selection(args.data_root, '70' if len(base['documents']) > 14 else '14', getattr(args, 'set', 'A'))
    failures = [d for d in base['documents'] if not d['success']]
    charged = base['reserved_or_reported_usd']
    if charged + len(failures)*args.page_cap_usd > args.max_usd:
        raise SystemExit('Combined first-pass plus recovery reservation exceeds the run allowance.')
    args.out.mkdir(parents=True, exist_ok=False)
    import shutil
    shutil.copytree(args.source/'candidate', args.out/'candidate')
    shutil.copyfile(args.source/'selected_original_cases.json', args.out/'selected_original_cases.json')
    (args.out/'docs').mkdir()
    profile = dict(scope=base_profile['scope']+' with one bounded failed-page recovery',
        first_pass=str(args.source), first_pass_profile=base_profile, azure=public_profile(config),
        max_usd=args.max_usd, recovery_selection='all extraction failures; no assertion labels used',
        recovery_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (args.out/'azure_profile.json').write_text(json.dumps(profile, indent=2), encoding='utf8')
    env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', AXTRACT_VERIFY='1')
    started = time.perf_counter()
    docs = {d['pdf']:d for d in base['documents']}
    print(f'Recovering {len(failures)} failed pages; first-pass reserved/reported ${charged:.4f}; combined allowance ${args.max_usd}', flush=True)
    def one(previous):
        pdf = previous['pdf']; index = list(docs).index(pdf)
        result = args.out/'docs'/f'{index:03}.json'
        cmd = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), 'worker',
               '--data-root', str(args.data_root), '--out', str(args.out), '--pdf', pdf, '--result', str(result)]
        t0 = time.perf_counter()
        with (args.out/'docs'/f'{index:03}.log').open('w', encoding='utf8') as log:
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                proc.wait(timeout=config['doc_seconds']+30)
            except subprocess.TimeoutExpired:
                import frozen70_benchmark as frozen
                frozen.kill_tree(proc); proc.wait()
        row = json.loads(result.read_text(encoding='utf8')) if result.exists() else dict(pdf=pdf,success=False,error='Recovery worker timeout/crash',reserved_or_reported_usd=args.page_cap_usd)
        row['first_pass_error'] = previous.get('error')
        row['recovery_wall_seconds'] = round(time.perf_counter()-t0, 3)
        row['wall_seconds'] = previous['wall_seconds'] + row['recovery_wall_seconds']
        incremental = row.get('reserved_or_reported_usd', args.page_cap_usd)
        row['reserved_or_reported_usd'] = previous.get('reserved_or_reported_usd', 0) + incremental
        row['cloud_requests'] = previous.get('cloud_requests',[]) + row.get('cloud_requests',[])
        return row, incremental
    with ThreadPoolExecutor(args.workers) as pool:
        for future in as_completed([pool.submit(one, previous) for previous in failures]):
            row, incremental = future.result(); docs[row['pdf']] = row; charged += incremental
            print(f'{row["pdf"]}: recovered={row["success"]}, combined reserved/reported=${charged:.4f}', flush=True)
    performance = dict(documents=list(docs.values()), wall_seconds=round(base['wall_seconds']+time.perf_counter()-started,2),
        first_pass_wall_seconds=base['wall_seconds'], recovery_wall_seconds=round(time.perf_counter()-started,2),
        successful=sum(d['success'] for d in docs.values()), reserved_or_reported_usd=round(charged,6),
        native_reused_pages=sum(d.get('native_reused_pages',0) for d in docs.values()),
        usage={key:sum(e.get('usage',{}).get(key,0) for d in docs.values() for p in d.get('cloud_requests',[]) for e in p['requests']) for key in ['input_tokens','output_tokens','total_tokens']})
    (args.out/'performance.json').write_text(json.dumps(performance,indent=2),encoding='utf8')
    with (args.out/'documents.jsonl').open('w',encoding='utf8') as stream:
        for row in docs.values(): stream.write(json.dumps(row)+'\n')
    if args.scorer:
        return score_run(args, performance, profile)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run_parser = sub.add_parser('run')
    recovery_parser = sub.add_parser('recover')
    score_parser = sub.add_parser('score')
    score_parser.add_argument('--out', type=Path, required=True)
    for p in [run_parser, sub.add_parser('worker'), recovery_parser]:
        p.add_argument('--data-root', type=Path, required=True)
        p.add_argument('--out', type=Path, required=True)
    run_parser.add_argument('--scope', choices=['14','70'], default='14')
    for p in [run_parser, recovery_parser]:
        p.add_argument('--set', choices=['A','B'], default='A', help='A: frozen manifest; B: all PDFs under --data-root')
    run_parser.add_argument('--workers', type=int, choices=[1,2,3,4], default=2)
    run_parser.add_argument('--max-usd', type=float, default=1.)
    run_parser.add_argument('--page-cap-usd', type=float, default=.02)
    recovery_parser.add_argument('--source', type=Path, required=True)
    recovery_parser.add_argument('--workers', type=int, choices=[1,2,3,4], default=2)
    recovery_parser.add_argument('--max-usd', type=float, default=1.50)
    recovery_parser.add_argument('--page-cap-usd', type=float, default=.075)
    for p in [run_parser, recovery_parser, score_parser]:
        p.add_argument('--scorer-python', type=Path, required=p is score_parser)
        p.add_argument('--scorer', type=Path, required=p is score_parser)
        p.add_argument('--scorer-packages', type=Path)
    worker_parser = sub.choices['worker']
    worker_parser.add_argument('--pdf', required=True)
    worker_parser.add_argument('--result', type=Path, required=True)
    args = parser.parse_args()
    if hasattr(args, 'data_root'): args.data_root = args.data_root.resolve()
    if hasattr(args, 'source'): args.source = args.source.resolve()
    args.out = args.out.resolve()
    for name in ('scorer_python', 'scorer', 'scorer_packages'):
        if getattr(args, name, None):
            setattr(args, name, getattr(args, name).resolve())
    if args.command in {'run','recover'}:
        import math
        if not math.isfinite(args.max_usd) or not math.isfinite(args.page_cap_usd) or not 0 < args.page_cap_usd <= 1 or args.max_usd <= 0:
            parser.error('Budgets must be finite positive amounts; page cap must not exceed USD 1.')
    return {'run':run, 'worker':worker, 'score':score_run, 'recover':recover}[args.command](args)


if __name__ == '__main__':
    raise SystemExit(main())
