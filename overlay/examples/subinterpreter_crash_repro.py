"""CRASH REFERENCE — DO NOT wire into tests/CI. Deliberately segfaults.

Demonstrates why PEP 734 sub-interpreters cannot render our tooltip: PIL's ``_imaging`` C extension
has process-global state that is not sub-interpreter-safe. Rendering the same panel from multiple
sub-interpreters concurrently corrupts it → SIGSEGV/SIGBUS (the signal varies run to run).

The chain of facts this pins (all measured 2026-07-26, see the `saitenka-render-parallelism` memo):

1. On the **free-threaded** build (3.14t, what we ship) the escape hatch is compiled out —
   ``_imp._override_multi_interp_extensions_check() cannot be used in the free-threaded build`` — so
   this can't even be attempted there. It requires a GIL-enabled CPython (e.g. Homebrew python3.14).
2. With the ``-1`` override, a *single* sub-interpreter imports PIL/numpy and renders a panel fine
   (numpy still warns it "does not properly support sub-interpreters").
3. Rendering from **N sub-interpreters in parallel** crashes. faulthandler pins it inside
   ``PIL.Image.new`` → ``_new`` (the C allocator), reached via the header speaker-icon draw.

Locking can't fix it (the racing state is PIL's module-wide C statics, not one call — a lock broad
enough to be safe serializes ~all rendering = 0x parallelism). The real fix is upstream in Pillow
(per-interpreter module state). For our workload the answer is free-threaded THREADS; see
``overlay.parallel`` and ``examples/bench_parallelism.py``.

Run (deliberately crashes — that is the point):

    SAITENKA_RUN_SUBINTERP_CRASH=1 \
      uv run --no-project --python /opt/homebrew/bin/python3.14 --with-editable '.[full]' \
      python examples/subinterpreter_crash_repro.py
"""

from __future__ import annotations

import os
import sys
import threading


def main() -> int:
    if os.environ.get("SAITENKA_RUN_SUBINTERP_CRASH") != "1":
        print(__doc__)
        print(">>> guarded: set SAITENKA_RUN_SUBINTERP_CRASH=1 to actually run it (it will crash).")
        return 0

    if getattr(sys, "_is_gil_enabled", lambda: True)() is False:
        print(
            "free-threaded build — the override hatch is unavailable here; run on GIL-enabled 3.14."
        )
        return 0

    # PEP 734, 3.14+ only; mypy pinned to 3.13
    from concurrent import interpreters  # type: ignore[attr-defined]

    print(
        f"Python {sys.version.split()[0]}  GIL={sys._is_gil_enabled()}  — reproducing the crash\n"
    )

    # --- step 1: a SINGLE sub-interpreter renders fine under the override -------------------------
    q = interpreters.create_queue()
    work = r"""
import _imp
_imp._override_multi_interp_extensions_check(-1)   # bypass the safety gate (unsafe on purpose)
import time
from overlay.panel import Entry, Definition, panel_rows, LazyPanel
e = Entry(headword=["猫"], defs=[Definition("D", ["猫。ネコ科の小型哺乳類。" + "かわいい説明。" * 3])])
LazyPanel(panel_rows(e, 640), 640).finish()        # warm (untimed)
t0 = time.perf_counter()
for _ in range(count):
    LazyPanel(panel_rows(e, 640), 640).finish()
q.put((time.perf_counter() - t0) * 1000)
"""
    solo = interpreters.create()
    solo.prepare_main(q=q, count=20)
    solo.exec(work)
    print(
        f"  1 sub-interpreter: OK, {q.get_nowait():.0f} ms for 20 renders (single-threaded is safe)"
    )
    solo.close()

    # --- step 2: N sub-interpreters rendering IN PARALLEL → segfault in PIL.Image.new -------------
    print(
        "\n  8 sub-interpreters rendering in parallel → expect SIGSEGV/SIGBUS in PIL.Image.new ..."
    )
    sys.stdout.flush()  # a hard crash won't flush buffered stdout
    interps = [interpreters.create() for _ in range(8)]
    for it in interps:
        it.prepare_main(q=q, count=40)
    threads = [threading.Thread(target=lambda it=it: it.exec(work)) for it in interps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    print("  (did not crash this run — memory corruption is nondeterministic; rerun to reproduce)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
