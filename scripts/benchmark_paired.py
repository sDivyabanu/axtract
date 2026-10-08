"""Alternate immutable baseline and optimized processes under identical conditions."""
import io,json,os,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
snapshot=ROOT/'reports/performance-baseline'
if not (snapshot/'backend/services/parse_service.py').exists():
    snapshot.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(subprocess.check_output(['git','archive','--format=zip','459b521','backend'],cwd=ROOT))) as archive:
        archive.extractall(snapshot)
files=sorted(p for p in (ROOT/'sample_files').iterdir() if p.suffix in ('.pdf','.docx','.pptx','.xlsx','.png','.jpg') and not p.name.startswith(('12_','13_')))
for i,p in enumerate(files):
    for version in (('baseline','optimized') if i%2==0 else ('optimized','baseline')):
        env=os.environ.copy()
        env['AXTRACT_BENCH_BACKEND']=str(ROOT/('reports/performance-baseline/backend' if version=='baseline' else 'backend'))
        tag=f'paired_{version}_{i:02d}'
        print(tag,p.name,flush=True)
        with (ROOT/'reports/performance'/f'{tag}.log').open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,'-B',str(ROOT/'scripts/benchmark_performance.py'),'--tag',tag,'--runs','5','--files',str(p.relative_to(ROOT))],env=env,stdout=log,stderr=log,check=True)
