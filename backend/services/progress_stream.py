"""Server-sent events for a parse job.

    event: start    {"stages": [{id, label, description}], "verify": bool}
    event: stage    {"id", "state", "seq", "elapsed_ms", "detail"?}      (many; real transitions only)
    event: result   the same JSON the non-streaming endpoint returns
    event: error    {"code", "message", "status_code"}                    (terminal, instead of result)
    : ping                                                               (comment; keeps proxies from closing an idle stream)

The job runs as its own task. If the client disconnects, the task is cancelled and the worker thread
stops at its next reported transition (see `progress.Cancelled`), so an abandoned upload does not keep
burning CPU. Events carry counts and fixed vocabulary only (see `progress._clean_detail`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from models.errors import AppError
from services import progress

logger = logging.getLogger(__name__)

PING_SECONDS = 15.0
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(jsonable_encoder(data), separators=(',', ':'))}\n\n".encode()


def verify_enabled() -> bool:
    return os.environ.get("AXTRACT_VERIFY", "1").strip().lower() not in ("0", "false", "off", "no")


async def event_stream(job: Callable[[], Awaitable[Any]], *, ping_seconds: float = PING_SECONDS) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    tracker = progress.Tracker(lambda ev: loop.call_soon_threadsafe(queue.put_nowait, ("stage", ev)))

    async def runner() -> None:
        progress.install(tracker)
        try:
            queue.put_nowait(("result", await job()))
        except AppError as exc:
            queue.put_nowait(("error", {"code": exc.code, "message": exc.message, "status_code": exc.status_code}))
        except HTTPException as exc:
            queue.put_nowait(("error", {"code": f"HTTP_{exc.status_code}", "message": str(exc.detail), "status_code": exc.status_code}))
        except (asyncio.CancelledError, progress.Cancelled):
            raise
        except Exception:  # noqa: BLE001 - the stream must end with a structured error, never a raw traceback
            logger.exception("streamed job failed")
            queue.put_nowait(("error", {"code": "INTERNAL_ERROR", "message": "Document processing failed.", "status_code": 500}))
        finally:
            queue.put_nowait(("end", None))

    task = asyncio.create_task(runner())
    try:
        yield sse("start", {"stages": progress.STAGES, "verify": verify_enabled()})
        while True:
            try:
                kind, payload = await asyncio.wait_for(queue.get(), ping_seconds)
            except asyncio.TimeoutError:
                yield b": ping\n\n"
                continue
            if kind == "end":
                break
            yield sse(kind, payload)
    finally:
        if not task.done():  # client went away (or the generator was closed): stop the work
            tracker.cancelled.set()
            task.cancel()
