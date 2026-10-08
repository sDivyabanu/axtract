"""Regression checks for cache isolation, equivalent numerics and bounded execution."""
import threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pytest
from utils.request_cache import request_cache, memo_text, _state, source_pdf
from utils.jobs import _execute
from models.errors import AppError


def test_text_cache_keeps_options_and_does_not_cross_requests():
    calls=[]
    @memo_text
    def render(text, upper=False):
        calls.append(text)
        return text.upper() if upper else text
    with request_cache():
        assert render('a') == render('a') == 'a'
        assert render('a',upper=True) == 'A'
        assert len(calls)==2
    assert _state.get() is None
    with request_cache(): render('a')
    assert len(calls)==3


def test_cache_bounds_and_cleanup_on_error():
    @memo_text
    def same(t): return t
    with pytest.raises(RuntimeError):
        with request_cache():
            for i in range(3000): same(str(i))
            assert len(_state.get()['memo']) == 2048
            same('x'*9000)
            assert len(_state.get()['memo']) == 2048
            raise RuntimeError()
    assert _state.get() is None


def test_ocr_exact_pixel_reuse_and_mutation_isolation(monkeypatch):
    from extractors import ocr_extractor as ocr
    calls=[]
    def engine(arr):
        calls.append(arr.copy())
        return [[[[0,0],[1,0],[1,1],[0,1]], 'text', .9]], [0.1]
    monkeypatch.setattr(ocr,'_get_ocr',lambda: engine)
    image=np.zeros((10,10,3),dtype=np.uint8)
    with request_cache():
        a=ocr.run_ocr(image)
        a[0][0][1]='changed'
        assert ocr.run_ocr(image)[0][0][1]=='text'
        image[0,0,0]=1
        ocr.run_ocr(image)
        assert len(calls)==2
    with request_cache(): ocr.run_ocr(image)
    assert len(calls)==3


def test_source_pdf_reuse_keeps_raw_independent_text(make_pdf):
    with request_cache():
        with source_pdf(make_pdf) as first:
            text=first.pages[0].extract_text()
        with source_pdf(make_pdf) as second:
            assert first is second
            assert second.pages[0].extract_text()==text
    with request_cache():
        with source_pdf(make_pdf) as third:
            assert third is not first


def test_stateful_office_extractors_are_isolated():
    from extractors.registry import get_extractor
    for ext in ('docx','pptx','xlsx'):
        assert get_extractor(ext) is not get_extractor(ext)


def test_busy_job_does_not_release_active_slot():
    started=threading.Event(); finish=threading.Event()
    def work():
        started.set(); assert finish.wait(5); return 42
    with ThreadPoolExecutor(1) as pool:
        task=pool.submit(_execute,work,())
        assert started.wait(5)
        try:
            with pytest.raises(AppError) as caught: _execute(lambda: None,())
            assert caught.value.code=='SERVER_BUSY'
        finally: finish.set()
        assert task.result()==42
    assert _execute(lambda: 7,())==7


def test_squared_colour_distance_matches_original():
    rng=np.random.default_rng(71)
    rgb=rng.integers(0,256,(120,170,3),dtype=np.uint8)
    for c in ([0.,0.,0.],[127.5,22.,252.5],[255.,255.,255.]):
        reference=np.linalg.norm(rgb.astype(int)-c,axis=2)<55
        channels=tuple(rgb[:,:,i].astype(float) for i in range(3))
        actual=sum((channel-value)**2 for channel,value in zip(channels,c))<55**2
        np.testing.assert_array_equal(actual,reference)


def test_concurrent_request_caches_are_separate():
    barrier=threading.Barrier(2)
    def work(value):
        with request_cache():
            _state.get()['memo']['key']=value
            barrier.wait(timeout=5)
            return _state.get()['memo']['key']
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(work,[1,2]))==[1,2]
