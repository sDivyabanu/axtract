"""Benchmark process parallelism without sharing PDF/model objects across threads."""
import argparse, concurrent.futures, json, multiprocessing, os, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,os.environ.get('AXTRACT_BENCH_BACKEND',str(ROOT/'backend')))

def page_job(arg):
    path,index=arg
    import pymupdf
    from extractors.pymupdf_extractor import PyMuPDFExtractor
    with pymupdf.open(path) as doc:
        return [b.model_dump(mode='json') for b in PyMuPDFExtractor()._extract_page(doc[index],index+1)]

def document_job(path):
    import io
    from fastapi import UploadFile
    from services.parse_service import parse_upload
    p=Path(path); start=time.perf_counter()
    response=parse_upload(UploadFile(filename=p.name,file=io.BytesIO(p.read_bytes())))
    return {'seconds':time.perf_counter()-start,'blocks':len(response.blocks),'validation':response.validation['status']}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--tag',required=True);args=ap.parse_args()
    path=str(ROOT/'sample_files/01_cross_page_table.pdf'); jobs=[(path,i) for i in range(4)]
    start=time.perf_counter(); serial=list(map(page_job,jobs)); serial_s=time.perf_counter()-start
    start=time.perf_counter()
    with concurrent.futures.ProcessPoolExecutor(2,mp_context=multiprocessing.get_context('spawn')) as pool:
        parallel=list(pool.map(page_job,jobs))
    parallel_s=time.perf_counter()-start
    rows=[]
    for workers in (1,2):
        with concurrent.futures.ProcessPoolExecutor(workers,mp_context=multiprocessing.get_context('spawn')) as pool:
            list(pool.map(document_job,[path]*workers*2))
            start=time.perf_counter(); outputs=list(pool.map(document_job,[path]*8)); elapsed=time.perf_counter()-start
            rows.append(dict(workers=workers,documents=8,seconds=elapsed,documents_per_second=8/elapsed,outputs=outputs))
    result=dict(page_parallelism=dict(serial_s=serial_s,two_process_s=parallel_s,identical=serial==parallel),throughput=rows)
    (ROOT/'reports/performance'/f'concurrency_{args.tag}.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)
if __name__=='__main__':main()
