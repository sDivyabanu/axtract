"""Bounded integration check on existing PDFs/assertions; never a full-corpus score.

Extraction runs one isolated child per PDF with a hard timeout and RAM guard.
Snapshot replay reads committed modules in memory; no original checkout is changed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
MODULES = [
 'services.vision.latex', 'extractors.ocr_runtime', 'extractors.ocr_extractor', 'extractors.table_geometry',
 'extractors.text_math', 'extractors.pymupdf_extractor', 'services.vision.models',
 'services.vision.pix2text_mfr','services.vision.line_math','services.scanned_math',
 'services.vision.chart_raster', 'services.table_service', 'services.layout_service',
 'services.markdown_service', 'services.region_router', 'services.parse_service',
]
def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def dump(path, obj):
    with path.open('x', encoding='utf-8') as f: json.dump(obj, f, indent=2)
def selection(args):
    if args.selection:
        fixed=json.loads(args.selection.read_text(encoding='utf-8'))
        paths=list(fixed['pdfs']) if 'pdfs' in fixed else [p for group in fixed.values() for p in group]
        return {p:digest(args.data_root/'pdfs'/p) for p in paths}
    original = json.loads((ROOT/'evaluation/table_accuracy_selection.json').read_text(encoding='utf-8'))
    chosen = {p:h for p,h in original['pdfs'].items() if not p.startswith('long_tiny_text/')}
    # These two scan-routed controls hit the RAM guard in the initial run;
    # retained in native tests, excluded identically from this digital-only pair.
    # Same first four arXiv PDFs as Claude's pre-existing frozen math sample.
    maths = ['arxiv_math/2503.04245_pg7.pdf','arxiv_math/2503.05334_pg17.pdf',
             'arxiv_math/2503.06230_pg7.pdf','arxiv_math/2503.08494_pg2.pdf']
    for pdf in maths: chosen[pdf] = digest(args.data_root/'pdfs'/pdf)
    return chosen

def load_snapshot(revision, source_directory=None):
    import types
    sys.path.insert(0, str(ROOT/'backend'))
    hashes = {}
    for name in MODULES:
        path = 'backend/'+name.replace('.', '/')+'.py'
        if source_directory:
            snapshot=source_directory/path
            if not snapshot.exists() and name in ("services.vision.pix2text_mfr","services.vision.line_math","services.scanned_math"):
                continue
            source=snapshot.read_bytes()
        elif revision:
            run = subprocess.run(['git','show',f'{revision}:{path}'],cwd=ROOT,capture_output=True)
            if run.returncode:
                if name in ('extractors.ocr_runtime','extractors.table_geometry','extractors.text_math','services.vision.pix2text_mfr','services.vision.line_math','services.scanned_math'): continue
                run.check_returncode()
            source = run.stdout
        else:
            if not (ROOT/path).exists() and name in ("services.vision.pix2text_mfr","services.vision.line_math","services.scanned_math"):
                continue
            source=(ROOT/path).read_bytes()
        hashes[path]=hashlib.sha256(source).hexdigest()
        if revision or source_directory:
            package, _, leaf = name.rpartition('.')
            import importlib
            parent=importlib.import_module(package)
            module=types.ModuleType(name)
            module.__file__=str(ROOT/path);module.__package__=package
            sys.modules[name]=module;setattr(parent,leaf,module)
            exec(compile(source,str(ROOT/path),'exec'),module.__dict__)
    return hashes

def worker(args):
    started=time.perf_counter(); hashes=load_snapshot(args.revision,args.source_directory)
    sys.path.insert(0,str(ROOT/'scripts'))
    from table_accuracy_sample import process_peak_working_set
    from services.markdown_service import blocks_to_markdown
    path=args.data_root/'pdfs'/args.pdf
    stages={}
    if args.mode=='adapter':
        import benchmark_axtract_olmocr as adapter
        captured=[]
        original_parse=adapter.parse_path
        def capture(*a,**kw):
            response=original_parse(*a,**kw);captured.append(response);return response
        adapter.parse_path=capture
        pipeline_started=time.perf_counter();cpu_started=time.process_time()
        adapter_result=adapter.process_single_pdf(path,Path(args.pdf),args.output,enable_verify=True)
        if not adapter_result['success']:
            raise RuntimeError(adapter_result['error'])
        response=captured[0]
        blocks=response.blocks;pages=response.page_count;errors=[e.model_dump(mode='json') for e in response.errors]
        markdown=None;validation=response.validation;stages['real_adapter_metrics']=adapter_result
    elif args.mode=='full':
        from services import parse_service as service
        # Timing wrappers call the unchanged implementation and preserve return values.
        for name in ('pre_scan','scan_hidden_content','route_regions','assign_reading_order','_attach_validation'):
            original=getattr(service,name)
            def measured(*a,_f=original,_n=name,**kw):
                t=time.perf_counter()
                try:return _f(*a,**kw)
                finally:stages[_n]=stages.get(_n,0)+time.perf_counter()-t
            setattr(service,name,measured)
        pipeline_started=time.perf_counter(); cpu_started=time.process_time()
        response=service.parse_path(path,path.name,budget_seconds=45,persistent_preview=False)
        blocks=response.blocks; pages=response.page_count; errors=[e.model_dump(mode='json') for e in response.errors]
        # All selected official PDFs contain one page; use the actual sanitized response.
        assert pages==1, 'Selection must remain single-page for exact response scoring'
        markdown=response.markdown
        validation=response.validation
    else:
        from extractors.pymupdf_extractor import PyMuPDFExtractor
        from services.table_service import enhance_table_block,merge_cross_page_tables
        from services.layout_service import assign_reading_order
        pipeline_started=time.perf_counter(); cpu_started=time.process_time()
        result=PyMuPDFExtractor().extract(path)
        blocks=assign_reading_order(merge_cross_page_tables([enhance_table_block(b) for b in result.blocks]))
        pages=result.page_count;errors=[e.model_dump(mode='json') for e in result.errors]
        markdown=blocks_to_markdown(blocks); validation=None
    md=args.output/Path(args.pdf).parent/(Path(args.pdf).stem+'_pg1_repeat1.md')
    md.parent.mkdir(parents=True,exist_ok=True)
    if markdown is not None:
        with md.open('x',encoding='utf-8') as f:f.write(markdown)
    dump(args.result,dict(pdf=args.pdf,seconds=time.perf_counter()-started,
         pipeline_seconds=time.perf_counter()-pipeline_started,cpu_seconds=time.process_time()-cpu_started,
         pages=pages,errors=errors,
         peak_working_set_bytes=process_peak_working_set(),stages=stages,source_sha256=hashes,
         validation=validation,blocks=[b.model_dump(mode='json') for b in blocks]))

def extract(args):
    chosen=selection(args);args.output.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,AXTRACT_VERIFY='1',AXTRACT_ORT_THREADS='2',AXTRACT_OCR_ARENA='0',
             OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    documents=[]
    for i,(pdf,expected) in enumerate(chosen.items()):
        assert digest(args.data_root/'pdfs'/pdf)==expected
        result=args.output/f'document_{i:02}.json'
        cmd=[sys.executable,str(Path(__file__).resolve()),'worker','--data-root',str(args.data_root),
             '--output',str(args.output),'--mode',args.mode,'--pdf',pdf,'--result',str(result)]
        if args.revision:cmd+=['--revision',args.revision]
        if args.source_directory:cmd+=['--source-directory',str(args.source_directory)]
        started=time.perf_counter(); stopped=None
        with (args.output/f'document_{i:02}.log').open('x',encoding='utf-8') as log:
            proc=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=log)
            while proc.poll() is None:
                if time.perf_counter()-started>75: stopped='hard_timeout_75s'
                # Windows counters cover only this child; never inspect/stop other processes.
                import ctypes
                class Memory(ctypes.Structure):
                    _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','total_page','avail_page','total_virtual','avail_virtual','extended')]
                mem=Memory();mem.length=ctypes.sizeof(mem)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)) and mem.available<300*1024**2:
                    stopped='resource_guard_available_ram_below_300MiB'
                if stopped:proc.terminate();proc.wait(timeout=10);break
                time.sleep(.25)
        row=json.loads(result.read_text(encoding='utf-8')) if result.exists() else dict(pdf=pdf,seconds=time.perf_counter()-started,errors=[stopped or f'worker_exit_{proc.returncode}'])
        row['input_sha256']=expected;row['exit_code']=proc.returncode
        documents.append({k:v for k,v in row.items() if k not in ('blocks','validation')})
        print(pdf,round(row['seconds'],3),row['errors'],flush=True)
        if stopped and 'resource_guard' in stopped:break
    sources={p.name:digest(p) for p in args.data_root.glob('*.jsonl')}
    dump(args.output/'performance.json',dict(mode=args.mode,revision=args.revision or 'uncommitted-integration',
         head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         configuration={k:env.get(k) for k in ('AXTRACT_VERIFY','AXTRACT_ORT_THREADS','AXTRACT_OCR_ARENA','OMP_NUM_THREADS','AXTRACT_SCANNED_MATH','AXTRACT_MFR_DIR','AXTRACT_FORMULA_SITE')}, source_directory=str(args.source_directory) if args.source_directory else None,
         expected_pdfs=chosen,case_source_hashes=sources,documents=documents))

def score(args):
    sys.path[:0]=[str(args.scorer_packages),str(args.scorer)]
    from olmocr.bench.tests import load_single_test
    perf=json.loads((args.output/'performance.json').read_text(encoding='utf-8'))
    results=[]
    for name,expected in perf['case_source_hashes'].items():
        path=args.data_root/name;assert digest(path)==expected
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():continue
            case=json.loads(line)
            if case['pdf'] not in perf['expected_pdfs']:continue
            md=args.output/Path(case['pdf']).parent/(Path(case['pdf']).stem+f"_pg{case['page']}_repeat1.md")
            try:passed,why=load_single_test(case).run(md.read_text(encoding='utf-8')) if md.exists() else (False,'Missing output')
            except Exception as exc:passed,why=False,f'Scorer error: {exc}'
            results.append(dict(id=case['id'],pdf=case['pdf'],passed=bool(passed),explanation=why,type=case['type']))
    summary={cat:dict(passed=sum(c['passed'] for c in results if c['pdf'].split('/')[0]==cat),total=sum(c['pdf'].split('/')[0]==cat for c in results)) for cat in sorted({c['pdf'].split('/')[0] for c in results})}
    dump(args.output/args.score_report,dict(summary=summary,cases=results,scorer_errors=sum(c['explanation'].startswith('Scorer error:') for c in results),scorer_commit=subprocess.check_output(['git','-C',str(args.scorer),'rev-parse','HEAD'],text=True).strip(),scorer_tests_sha256=digest(args.scorer/'olmocr/bench/tests.py'),performance=perf))
    print(json.dumps(summary))

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['extract','worker','score'])
    parser.add_argument('--data-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['native','full','adapter'],default='full')
    parser.add_argument('--revision')
    parser.add_argument('--source-directory',type=Path)
    parser.add_argument('--selection',type=Path)
    parser.add_argument('--score-report',default='official_case_results.json')
    parser.add_argument('--pdf');parser.add_argument('--result',type=Path)
    parser.add_argument('--scorer',type=Path);parser.add_argument('--scorer-packages',type=Path)
    args=parser.parse_args()
    if args.revision:args.revision=subprocess.check_output(['git','rev-parse',args.revision],cwd=ROOT,text=True).strip()
    {'worker':worker,'extract':extract,'score':score}[args.action](args)
if __name__=='__main__':main()
