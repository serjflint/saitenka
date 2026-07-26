"""Executor policy for CPU-bound panel rendering — threads on free-threading, processes on a GIL build.

The tooltip render is ~78% FreeType glyph work (``getmask2``/``getlength``), and how to parallelise it
depends entirely on whether this CPython has the GIL (measured 2026-07-26; see the
``saitenka-render-parallelism`` memo and ``examples/bench_parallelism.py``):

- **Free-threaded (3.14t, the build we ship): threads.** FreeType releases the GIL and faces are
  thread-local (``fonts.py``), so threads render in parallel (~5x small / ~3x big blocks) with **zero
  copy** — the rendered image stays a shared-memory reference, no serialization. This is what
  :meth:`overlay.render.banded.WindowedPanel.render_ahead` already does.
- **GIL build: processes.** Threads would serialise on the GIL, so fall back to a process pool for true
  parallelism. The cost is real and the caller must design for it: tasks and results cross a pickle
  boundary (~120 µs to ship a 540 KB bitmap back, and it grows with bitmap size), and per-worker state
  (the dictionary DB) must be rebuilt in an ``initializer`` since nothing is shared. Only worth it for
  **stateless batch** rendering of independent panels — NOT the shared-cache windowed engine, which is
  threads-only by construction.

Sub-interpreters are deliberately absent: PIL's ``_imaging`` C extension segfaults when rendered from
multiple interpreters (``examples/subinterpreter_crash_repro.py``), on every Python version and backport.
"""

from __future__ import annotations

import sys
from concurrent.futures import Executor, ProcessPoolExecutor, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def is_free_threaded() -> bool:
    """True on a no-GIL (free-threaded) CPython, where threads execute Python in parallel. Falls back
    to ``False`` (assume a GIL) on interpreters without ``sys._is_gil_enabled`` (< 3.13)."""
    probe = getattr(sys, "_is_gil_enabled", None)
    return probe() is False if probe is not None else False


def pick_executor(
    max_workers: int,
    *,
    initializer: Callable[..., object] | None = None,
    initargs: tuple[Any, ...] = (),
) -> Executor:
    """A pool for CPU-bound render work, chosen by build: a :class:`ThreadPoolExecutor` when
    free-threaded (shared memory, no copy — ``initializer`` is unnecessary and skipped), otherwise a
    :class:`ProcessPoolExecutor` (``initializer``/``initargs`` rebuild per-process state, and tasks must
    be picklable with serializable results). Use as a context manager."""
    if is_free_threaded():
        return ThreadPoolExecutor(max_workers)
    return ProcessPoolExecutor(max_workers, initializer=initializer, initargs=initargs)
