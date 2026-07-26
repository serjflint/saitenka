"""Can the PIL-bound tooltip render be spread across cores?

A py-spy profile of the whole-panel render (``poe vocab-pyspy``) puts ~78% of the CPU in
FreeType glyph work — ``getmask2`` (rasterize, 53%) + ``getlength`` (measure, 24%). This experiment
asks whether that parallelizes, comparing four executors on the same real-episode word list
(``examples/vocab.json``), rendering each word's full panel (``entry_for`` pre-built out of the timed
loop where possible):

- **serial** — one thread, the baseline.
- **threads** — free-threaded (3.14t, GIL off): FreeType releases the GIL and this repo gives each
  thread its own faces (``fonts.py`` ``threading.local``), so unlike ``msgspec`` decode (which holds the
  GIL, ~0.94x) this scales — but sub-linearly (memory bandwidth + per-thread font caches).
- **subinterpreters** (PEP 734, stdlib ``concurrent.interpreters`` on 3.14) — **does not work here**:
  ``PIL._imaging`` (and numpy) refuse to import in a sub-interpreter (no ``Py_mod_multiple_interpreters``
  opt-in). Reported as UNSUPPORTED, not a number. Same blocker for the ``interpreters-pep-734`` /
  ``backports.interpreters`` / ``extrainterpreters`` PyPI backports — they wrap the same machinery.
- **process pool** (hot) — true parallelism with no shared interpreter; workers pre-import + open the DB
  in an initializer so we time steady-state render, not spawn. Cost: N× RSS and IPC (kept tiny: pass the
  token as strings, return the panel height as an int).

Run: ``uv run --extra full python examples/bench_parallelism.py`` (``--words N`` to size the batch).
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

# Per-worker state. Threads share this module global (set in main); each ProcessPool worker sets its own
# copy in the initializer. Sub-interpreters would each need their own, but they can't import PIL at all.
_ds = None


def _build_ds():
    from overlay.app.config import load_config
    from overlay.app.dictdb import DictionaryDb
    from overlay.app.dictionary import DictionarySet

    cfg = load_config()
    db = DictionaryDb.open()
    return DictionarySet.from_db(
        db, list(cfg.get("dicts") or []), list(cfg.get("freq") or []), list(cfg.get("pitch") or [])
    )


def _proc_init():
    global _ds
    _ds = _build_ds()


def _render(tok_fields: tuple[str, str, str, str]) -> int:
    """Render one word's full panel; returns its height. ``entry_for`` (lookup+decode, ~1% of cost) is
    inside the timed loop here so a ProcessPool worker does self-contained work over a shareable tuple."""
    from overlay.app.tokenize import Token
    from overlay.panel import LazyPanel, panel_rows

    assert _ds is not None, "worker not initialised"
    surface, lemma, reading, pos = tok_fields
    tok = Token(surface, lemma, reading, pos, 0, len(surface))
    return LazyPanel(panel_rows(_ds.entry_for(tok), 640), 640).finish().height


def _rss_mb() -> float:
    import resource

    div = 1024 * 1024 if sys.platform == "darwin" else 1024
    self_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / div
    child_rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / div
    return self_rss + child_rss


def _subinterpreter_probe() -> str:
    """Try PEP 734 sub-interpreters; return why they're unusable here (PIL won't import)."""
    try:
        from concurrent import interpreters
    except Exception as e:
        return f"no stdlib concurrent.interpreters ({type(e).__name__})"
    interp = interpreters.create()
    try:
        interp.exec("from PIL import Image")
    except Exception as e:
        msg = str(e).splitlines()[-1] if str(e) else type(e).__name__
        return f"UNSUPPORTED — {msg[:90]}"
    finally:
        interp.close()
    return "PIL imported (unexpected — re-evaluate)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--words", type=int, default=48, help="how many words from vocab.json to render"
    )
    ap.add_argument("--reps", type=int, default=3, help="repetitions; the min is reported")
    ap.add_argument(
        "--vocab", default=str(Path(__file__).with_name("vocab.json")), help="frozen word list"
    )
    args = ap.parse_args()

    import json
    import os

    global _ds
    _ds = _build_ds()
    words = json.loads(Path(args.vocab).read_text(encoding="utf-8"))
    toks = [tuple(w) for w in words[: args.words]]

    def timed(fn) -> float:
        best = float("inf")
        for _ in range(args.reps):
            t0 = time.perf_counter()
            fn()
            best = min(best, (time.perf_counter() - t0) * 1000)
        return best

    gil = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else None
    print(
        f"Python {sys.version.split()[0]}  GIL={'off' if gil is False else gil}  cores={os.cpu_count()}"
    )
    print(f"rendering {len(toks)} real-word panels, {args.reps} reps (min)\n")

    for t in toks[:8]:  # warm the main thread's font cache + OS page cache
        _render(t)

    serial = timed(lambda: [_render(t) for t in toks])
    print(f"  {'serial (1 thread)':<24}{serial:9.1f} ms   1.00x")

    for w in (2, 4, 8):

        def run_threads(w=w):
            with ThreadPoolExecutor(max_workers=w) as ex:
                list(ex.map(_render, toks))

        ms = timed(run_threads)
        print(
            f"  threads x{w:<15}{ms:9.1f} ms   {serial / ms:.2f}x  ({serial / ms / w * 100:.0f}% eff)"
        )

    print(f"\n  subinterpreters         {_subinterpreter_probe()}\n")

    for w in (2, 4, 8):
        # Hot pool: build + warm the workers ONCE, then time the batch (steady-state, not spawn).
        with ProcessPoolExecutor(max_workers=w, initializer=_proc_init) as ex:
            list(ex.map(_render, toks[:w]))  # warm each worker's imports + DB
            ms = timed(lambda ex=ex: list(ex.map(_render, toks)))
        print(
            f"  process pool x{w:<11}{ms:9.1f} ms   {serial / ms:.2f}x  ({serial / ms / w * 100:.0f}% eff)"
        )

    # The shipping policy (overlay.parallel): threads on free-threading, processes on a GIL build.
    from overlay.parallel import is_free_threaded, pick_executor

    choice = "ThreadPool (free-threaded)" if is_free_threaded() else "ProcessPool (GIL)"
    with pick_executor(4, initializer=_proc_init) as ex:
        list(ex.map(_render, toks[:4]))  # warm (a no-op for threads)
        ms = timed(lambda ex=ex: list(ex.map(_render, toks)))
    print(f"\n  policy pick_executor(4) → {choice}: {ms:9.1f} ms   {serial / ms:.2f}x")
    print(f"  peak RSS (self+children): {_rss_mb():.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
