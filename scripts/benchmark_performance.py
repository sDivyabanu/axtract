"""Repeatable full-pipeline benchmark; no remote services or server required."""
import argparse, cProfile, ctypes, hashlib, io, json, os, pstats, statistics, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.environ.get('AXTRACT_BENCH_BACKEND', str(ROOT / 'backend')))
os.environ['AXTRACT_VERIFY'] = '1'
from fastapi import UploadFile
from services.parse_service import parse_upload

def peak_mb():
    if os.name != 'nt':
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != 'darwin' else 1048576)
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [(n, ctypes.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    c = Counters(); c.cb = ctypes.sizeof(c)
    k = ctypes.WinDLL('kernel32'); k.GetCurrentProcess.restype = wintypes.HANDLE
    ps = ctypes.WinDLL('psapi'); ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    ps.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 1048576

def normalized(value):
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in ('document_id','processing_time_ms','timings_ms','timing_ms')}
    if isinstance(value, list): return [normalized(v) for v in value]
    return value

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--tag',required=True); ap.add_argument('--runs',type=int,default=3); ap.add_argument('--files',nargs='*'); args=ap.parse_args()
    out=ROOT/'reports'/'performance'; out.mkdir(parents=True,exist_ok=True)
    files=[ROOT/x for x in args.files] if args.files else sorted(p for p in (ROOT/'sample_files').iterdir() if p.suffix in ('.pdf','.docx','.pptx','.xlsx','.png','.jpg') and not p.name.startswith(('12_','13_')))
    results=[]
    for path in files:
        raw=path.read_bytes(); runs=[]
        for i in range(args.runs):
            t=time.perf_counter()
            try:
                response=parse_upload(UploadFile(filename=path.name,file=io.BytesIO(raw)))
                parse_ms=(time.perf_counter()-t)*1000; s=time.perf_counter(); serialized=response.model_dump_json(); serialization_ms=(time.perf_counter()-s)*1000
                data=json.loads(serialized); norm=normalized(data)
                (out/f'{args.tag}_{path.stem}_{i}.json').write_text(json.dumps(norm,ensure_ascii=False,indent=2),encoding='utf-8')
                runs.append(dict(parse_ms=parse_ms,serialization_ms=serialization_ms,peak_process_mb=peak_mb(),verify_ms=(data.get('validation') or {}).get('timings_ms',{}),blocks=len(data['blocks']),status=data['status'],validation=(data.get('validation') or {}).get('status'),sha256=hashlib.sha256(json.dumps(norm,sort_keys=True).encode()).hexdigest()))
            except Exception as e: runs.append(dict(error=f'{type(e).__name__}: {e}',elapsed_ms=(time.perf_counter()-t)*1000))
            print(path.name,i,json.dumps(runs[-1]),flush=True)
        prof=cProfile.Profile()
        try:
            prof.runcall(parse_upload,UploadFile(filename=path.name,file=io.BytesIO(raw)))
        except Exception: pass
        stream=io.StringIO(); pstats.Stats(prof,stream=stream).strip_dirs().sort_stats('cumulative').print_stats(45)
        (out/f'{args.tag}_{path.stem}_profile.txt').write_text(stream.getvalue(),encoding='utf-8')
        stats=pstats.Stats(prof).stats
        profile=[dict(file=Path(k[0]).name,line=k[1],function=k[2],calls=v[1],self_s=v[2],cumulative_s=v[3]) for k,v in stats.items()]
        (out/f'{args.tag}_{path.stem}_stages.json').write_text(json.dumps(profile,indent=2),encoding='utf-8')
        results.append(dict(file=str(path.relative_to(ROOT)),input_sha256=hashlib.sha256(raw).hexdigest(),runs=runs))
        (out/f'{args.tag}.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
if __name__=='__main__': main()
