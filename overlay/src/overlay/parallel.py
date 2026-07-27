"""Executor policy for CPU-bound panel rendering — threads on free-threading, processes on a GIL build.

The tooltip render is ~78% FreeType glyph work (``getmask2``/``getlength``), and how to parallelise it
depends entirely on whether this CPython has the GIL (measured 2026-07-26; see the
``saitenka-render-parallelism`` memo and ``examples/bench_parallelism.py``):

- **Free-threaded (3.14t, the build we ship): threads.** FreeType releases the GIL and faces are
  thread-local (``fonts.py``), so threads render in parallel (~5x small / ~3x big blocks) with **zero
  copy** — the rendered image stays a shared-memory reference, no serialization. This is what
  :meth:`overlay.render.banded.WindowedPanel.render_ahead` already does.
- **GIL build: processes.** Threads would serialise on the GIL — measured *worse* than serial for this
  CPU-bound work (pure overhead, zero parallelism; ``examples/bench_parallelism.py`` run on a standard
  3.13 build), so fall back to a process pool for real parallelism (3.6-4.4x measured). The cost is
  real and the caller must design for it: tasks and results cross a pickle boundary (~120 µs to ship a
  540 KB bitmap back, and it grows with bitmap size — assessed as negligible at the block sizes this
  engine actually renders), and per-worker state (the dictionary DB) must be rebuilt in an
  ``initializer`` since nothing is shared. The windowed engine (``render/banded.py``) IS process-pool
  capable on a GIL build for its def-body blocks specifically — see ``panel.BodyRenderArgs``/
  ``panel.render_body_block``, which carry plain picklable data instead of the closures
  ``panel_rows()`` uses everywhere else; rows without ``body_args`` still need a thread/sequential path.

Sub-interpreters are deliberately absent: PIL's ``_imaging`` C extension segfaults when rendered from
multiple interpreters (``examples/subinterpreter_crash_repro.py``), on every Python version and backport.
"""

from __future__ import annotations

import sys
import threading
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


_shared: Executor | None = None
_shared_is_ft: bool | None = None
_shared_lock = threading.Lock()


def shared_executor(
    max_workers: int = 4,
    *,
    initializer: Callable[..., object] | None = None,
    initargs: tuple[Any, ...] = (),
) -> Executor:
    """A process-wide :func:`pick_executor` pool, created ONCE and reused for the rest of the
    process's life — unlike a bare ``pick_executor()`` call, which is meant to be used as a context
    manager and torn down after. A recurring background job (prefetch, banded scroll-ahead) that calls
    this every time it has work would otherwise pay process-spawn cost on every single call on a GIL
    build, which would likely erase the parallelism win entirely. ``max_workers``/``initializer`` only
    take effect on the first call for the CURRENT ``is_free_threaded()`` value — later calls just
    return the existing pool, UNLESS ``is_free_threaded()`` disagrees with what's cached, in which case
    it's rebuilt. In real use the GIL state can't change mid-process, so that's dead weight there —
    it's for the test suite, which monkeypatches ``is_free_threaded`` per test to simulate both builds;
    without this check, whichever test calls this first would leak its pool type (thread or process)
    into every later test in the same worker, e.g. a bound method submitted to a stale process pool
    dies with a ``PicklingError`` that has nothing to do with the test that actually hit it. Call
    :func:`shutdown_shared_executor` to tear it down explicitly (a clean app exit doesn't need to —
    daemon-owned resources die with the process)."""
    global _shared, _shared_is_ft
    with _shared_lock:
        ft_now = is_free_threaded()
        if _shared is None or _shared_is_ft != ft_now:
            if _shared is not None:
                _shared.shutdown(wait=False)
            _shared = pick_executor(max_workers, initializer=initializer, initargs=initargs)
            _shared_is_ft = ft_now
        return _shared


def shutdown_shared_executor(wait: bool = True) -> None:
    global _shared, _shared_is_ft
    with _shared_lock:
        if _shared is not None:
            _shared.shutdown(wait=wait)
            _shared = None
            _shared_is_ft = None
