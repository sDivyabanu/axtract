"""Bound CPU jobs per server process; cancelled callers cannot release running work."""
import threading
from starlette.concurrency import run_in_threadpool
from models.errors import AppError

# PyMuPDF and the shared OCR engines are not safe for unrestricted thread concurrency.
# Independent server processes may each admit one job; no unbounded in-process queue.
_slot = threading.BoundedSemaphore(1)

def _execute(fn, args):
    if not _slot.acquire(blocking=False):
        raise AppError('SERVER_BUSY', 'A document is already processing. Retry shortly.', status_code=503)
    try:
        return fn(*args)
    finally:
        _slot.release()

async def run_parse_job(fn, *args):
    return await run_in_threadpool(_execute, fn, args)
