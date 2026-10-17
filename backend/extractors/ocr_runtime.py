"""Thread budget for the RapidOCR engine: same models, same inputs, same results, far less wasted CPU.

onnxruntime sizes each session's thread pool to the physical core count and its threads spin-wait. Measured on
this project's fixtures that burned ~8x more CPU time than wall time (RapidOCR on a 1850x1472 scan: 40 CPU-s
for 5 s) while 4 threads finished in the same wall time with half the CPU (2 threads were ~20% slower, 6-8 no
faster). Capping the pool at 4 therefore frees CPU/power for the rest of the pipeline and other applications and
keeps concurrent work from oversubscribing the machine. It changes scheduling only: models, batching, pre- and
post-processing are untouched.

Not on by default: re-enabling onnxruntime's CPU memory arena (RapidOCR turns it off). It makes documents with new
page shapes 20-45% faster but grew peak RAM from 1.2 GB to 2.0 GB over the fixture set. Opt in with
AXTRACT_OCR_ARENA=1 when RAM is plentiful.

Environment: AXTRACT_ORT_THREADS=<n> overrides the cap; AXTRACT_ORT_THREADS=0 restores the library default.
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_THREADS = 4
_lock = threading.Lock()
_installed = False


def thread_cap() -> int:
    """Intra-op threads per OCR session; 0 means leave onnxruntime's default alone."""
    raw = os.environ.get("AXTRACT_ORT_THREADS", "").strip()
    if not raw:
        return min(DEFAULT_THREADS, os.cpu_count() or DEFAULT_THREADS)
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_THREADS


def _arena_opt_in() -> bool:
    """AXTRACT_OCR_ARENA=1: trade RAM for speed on new document shapes (off by default).

    Measured cold (new shapes, which is what each new document brings): 20-45% faster on scan-heavy files,
    but +130-170 MB peak and 0.3-0.6 GB retained RAM growing toward 2 GB over a session.
    """
    return os.environ.get("AXTRACT_OCR_ARENA", "0").strip() == "1"


def install() -> None:
    """Apply the cap once, before RapidOCR() builds its sessions. Never raises: on any problem the stock engine stays."""
    global _installed
    cap = thread_cap()
    if _installed or cap == 0:
        return
    with _lock:
        if _installed:
            return
        try:
            import onnxruntime as ort
            from rapidocr_onnxruntime import utils as rapid_utils

            class _CappedSession(ort.InferenceSession):
                def __init__(self, path_or_bytes, sess_options=None, *args, **kwargs):
                    if sess_options is not None and sess_options.intra_op_num_threads == 0:
                        sess_options.intra_op_num_threads = cap
                        sess_options.inter_op_num_threads = 1
                        if _arena_opt_in():
                            sess_options.enable_cpu_mem_arena = True
                            sess_options.enable_mem_pattern = False
                    super().__init__(path_or_bytes, sess_options, *args, **kwargs)

            rapid_utils.InferenceSession = _CappedSession  # only RapidOCR's sessions; other ONNX users are untouched
            _installed = True
            logger.info("RapidOCR onnxruntime sessions capped at %d threads", cap)
        except Exception:  # noqa: BLE001 - an optimisation only; the stock engine must keep working
            logger.exception("OCR thread cap unavailable; using the stock engine")
