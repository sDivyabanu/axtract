"""Model startup and fake-service persistence instrumentation; never uses remote credentials."""
import io,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,os.environ.get('AXTRACT_BENCH_BACKEND',str(ROOT/'backend')))
from extractors.ocr_extractor import _get_ocr
start=time.perf_counter(); _get_ocr(); cold=(time.perf_counter()-start)*1000
start=time.perf_counter(); _get_ocr(); warm=(time.perf_counter()-start)*1000
import pytest
from tests.test_documents_api import env
from routers import documents
patch=pytest.MonkeyPatch(); fixture=env.__wrapped__(patch)
client,*_=next(fixture)
stages={}
for name in ('upsert_profile','create_document','create_document_version','create_processing_run','save_document_output','complete_processing_run','update_document_status'):
    original=getattr(documents.db,name)
    def make_wrapper(fn,key):
        async def wrapped(*args,**kwargs):
            start=time.perf_counter()
            try:return await fn(*args,**kwargs)
            finally:stages[key]=stages.get(key,0)+(time.perf_counter()-start)*1000
        return wrapped
    patch.setattr(documents.db,name,make_wrapper(original,name))
path=ROOT/'sample_files/09_financials.xlsx'; start=time.perf_counter()
response=client.post('/api/documents',files={'file':(path.name,path.read_bytes(),'application/octet-stream')})
elapsed=(time.perf_counter()-start)*1000
try:next(fixture)
except StopIteration:pass
patch.undo()
result=dict(ocr_model_init_ms=cold,ocr_reuse_ms=warm,fake_persistence_stages_ms=stages,http_in_process_ms=elapsed,status_code=response.status_code,warning='Database/storage are in-memory test doubles, not a measurement of remote service latency.')
print(json.dumps(result))
(ROOT/'reports/performance'/f'initialization_{sys.argv[1]}.json').write_text(json.dumps(result,indent=2))
