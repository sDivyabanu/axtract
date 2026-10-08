"""Bounded request-local intermediates; never share document content across requests."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

_state = ContextVar('parse_intermediates', default=None)

@contextmanager
def request_cache():
    state = {'memo': {}, 'chars': 0, 'pdfs': {}, 'ocr': {}}
    token = _state.set(state)
    try:
        yield
    finally:
        try:
            for pdf in state['pdfs'].values():
                pdf.close()
        finally:
            _state.reset(token)

def cached_request(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with request_cache():
            return fn(*args, **kwargs)
    return wrapped

def memo_text(fn):
    """Cache immutable text results, including every option in the key."""
    @wraps(fn)
    def wrapped(text, *args, **kwargs):
        state = _state.get()
        if state is None or not isinstance(text, str) or len(text) > 8192:
            return fn(text, *args, **kwargs)
        key = (fn.__module__, fn.__name__, text, args, tuple(sorted(kwargs.items())))
        cache = state['memo']
        if key in cache:
            return cache[key]
        value = fn(text, *args, **kwargs)
        size = len(text) + len(value)
        if len(cache) < 2048 and state['chars'] + size <= 2_000_000:
            cache[key] = value
            state['chars'] += size
        return value
    return wrapped

def acquire_pdf(path):
    """Reuse raw pdfplumber parsing, not extraction results or validation verdicts.

    Verify still reads source text through independent PDFium and pdfplumber.
    Large files bypass retention to avoid holding a second large parsed document.
    """
    import pdfplumber
    state = _state.get()
    key = str(Path(path).resolve())
    if state is not None and key in state['pdfs']:
        return state['pdfs'][key]
    pdf = pdfplumber.open(str(path))
    if state is not None and Path(path).stat().st_size <= 10 * 1024 * 1024 and len(pdf.pages) <= 30:
        state['pdfs'][key] = pdf
    return pdf

def release_pdf(pdf):
    state = _state.get()
    if state is None or not any(p is pdf for p in state['pdfs'].values()):
        pdf.close()

@contextmanager
def source_pdf(path):
    pdf = acquire_pdf(path)
    try:
        yield pdf
    finally:
        release_pdf(pdf)


def close_source_path(path):
    """Release OS file handles before parse_upload removes its temporary source."""
    state = _state.get()
    if state is not None and path is not None:
        pdf = state['pdfs'].pop(str(Path(path).resolve()), None)
        if pdf is not None:
            pdf.close()
