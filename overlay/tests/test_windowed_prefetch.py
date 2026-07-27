"""Stage 6: background render-ahead + skeleton + shared-cache concurrency.

No real mpv — the worker is a plain thread, the cache is the shared object. Correctness under
free-threading is the point, so the concurrency test runs the worker and the main thread against one
``WindowedPanel`` and asserts the final frames still match ``render_panel`` exactly. Runs in the
whole-suite ``poe test-ft`` pass for the shared-cache race check."""

from __future__ import annotations

import os
import threading

import numpy as np
import pytest

from overlay.panel import Definition, Entry, panel_rows, render_panel
from overlay.render.banded import WindowedPanel

WIDTH = 384

# The GIL can't be toggled after interpreter startup, and fugashi re-enables it on first tokenize — so
# a test that needs real parallelism (not a serialised GIL crawl) is only meaningful when PYTHON_GIL=0
# was set at launch. Same gate as tests/test_ft_gil.py; under `poe test-ft` it's on, elsewhere it skips.
GIL_FORCED_OFF = os.environ.get("PYTHON_GIL") == "0"


def _entry(n_defs: int = 16) -> Entry:
    return Entry(
        headword=["掛ける", {"tag": "rt", "content": "かける"}],
        defs=[
            Definition(f"辞書{i}", [f"意味{i}：長い説明文が縦に伸びていく本文。" * 2])
            for i in range(n_defs)
        ],
    )


def test_render_ahead_prefetches_the_next_blocks_downward():
    entry = _entry(16)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(0, 260)
    before = wp.cached_blocks
    n = wp.render_ahead(0, 260, direction=1, max_blocks=3)
    assert n == 3  # rendered exactly the requested look-ahead
    assert wp.cached_blocks == before + 3  # …and they are now warm in the cache


def test_parallel_and_sequential_render_ahead_agree(monkeypatch):
    # The free-threaded (thread-pool) and GIL (process-pool, def-body blocks only) paths must cache
    # the SAME blocks and preserve pixel parity — the progressive _store path can't drift from
    # _ensure_block. Dispatch is derived from the executor shared_executor() ACTUALLY returns (see
    # banded.py's _render_ahead_parallel), so simulate each build by patching
    # overlay.parallel.is_free_threaded (what shared_executor's own pick_executor() call reads) and
    # forcing a fresh pool per iteration — a stale pool from a differently-configured earlier test
    # must never leak in (that mismatch used to crash with an opaque PicklingError).
    import overlay.parallel as PA
    from overlay.body_block import render_body_block

    entry = _entry(20)
    ref = render_panel(entry, width=WIDTH)
    results = {}
    try:
        for ft in (True, False):
            PA.shutdown_shared_executor()
            monkeypatch.setattr(PA, "is_free_threaded", lambda ft=ft: ft)
            wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, render_block_fn=render_body_block)
            wp.viewport(0, 260)  # cache the head
            n = wp.render_ahead(0, 260, direction=1, max_blocks=6, workers=2)
            results[ft] = (n, wp.measured)
            # the pre-rendered ahead blocks composite pixel-identically to the one-shot crop
            win = wp.viewport(0, 260)
            assert (
                np.abs(
                    np.asarray(win, np.int16) - np.asarray(ref.crop((0, 0, WIDTH, 260)), np.int16)
                ).max()
                == 0
            )
    finally:
        PA.shutdown_shared_executor()
    assert results[True] == results[False]  # same block count + measured prefix either way


def test_render_ahead_is_cancellable_between_blocks():
    entry = _entry(16)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    wp.viewport(0, 260)
    calls = [0]

    def cancel() -> bool:
        calls[0] += 1
        return calls[0] > 2  # allow two blocks, then cancel (a word switch bumps the generation)

    n = wp.render_ahead(0, 260, direction=1, max_blocks=8, should_cancel=cancel)
    assert n == 2  # stopped early — did not render all 8


def test_skeleton_frame_is_always_a_full_atomic_frame():
    entry = _entry(16)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    # even before ANY block is rendered, the frame is full-size (never a partial upload)
    frame = wp.skeleton_frame(500, 260)
    assert frame.size == (WIDTH, 260)


def test_skeleton_converges_to_the_exact_viewport_once_filled():
    entry = _entry(16)
    total = render_panel(entry, width=WIDTH).height
    scroll, vh = min(400, total - 260), 260
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)  # no cap → nothing evicted
    exact = np.asarray(wp.viewport(scroll, vh))  # renders every visible block
    skel = np.asarray(wp.skeleton_frame(scroll, vh))  # all visible now cached → no skeleton bands
    assert np.array_equal(
        skel, exact
    )  # the skeleton frame is pixel-exact once the blocks have landed


def test_skeleton_draws_bands_only_where_blocks_are_missing():
    entry = _entry(16)
    total = render_panel(entry, width=WIDTH).height
    scroll = total // 2
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH)
    skel = np.asarray(wp.skeleton_frame(scroll, 260))  # nothing rendered at this offset yet
    # the skeleton fill colour (214) appears (bands drawn) — the frame is not just background
    assert (skel[:, :, 0] == 214).any()


@pytest.mark.integration
@pytest.mark.timeout(30)
@pytest.mark.skipif(
    not GIL_FORCED_OFF, reason="needs real free-threading (run under `poe test-ft`)"
)
def test_concurrent_worker_and_main_stay_consistent():
    # A prefetch worker renders ahead while the main thread composites viewports over the SAME panel;
    # the shared cache must not corrupt and every main-thread frame must still equal render_panel.
    entry = _entry(24)
    total = render_panel(entry, width=WIDTH).height
    ref = render_panel(entry, width=WIDTH)
    wp = WindowedPanel(panel_rows(entry, WIDTH), WIDTH, max_cached_blocks=6)
    vh = 260
    stop = threading.Event()
    errors: list[BaseException] = []

    def worker():
        try:
            s = 0
            while not stop.is_set():
                wp.render_ahead(s, vh, direction=1, max_blocks=2)
                s = (s + 80) % max(1, total - vh)
        except BaseException as e:  # noqa: BLE001  # surface any thread crash (incl. SystemExit) to the test assertion
            errors.append(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        for _ in range(3):
            for scroll in range(0, total - vh, 40):
                win = np.asarray(wp.viewport(scroll, vh), np.int16)
                exp = np.asarray(ref.crop((0, scroll, WIDTH, scroll + vh)), np.int16)
                assert np.abs(win - exp).max() == 0, f"windowed != crop at scroll={scroll}"
    finally:
        stop.set()
        t.join(timeout=5)
    assert not errors  # the worker never raised (no dict-changed-size / torn read under the lock)
