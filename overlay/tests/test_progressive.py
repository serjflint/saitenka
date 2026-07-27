"""Progressive startup: reader runs subs-only, then injects deps (coloring/dicts/mining)."""

from __future__ import annotations

from util import FakeIPC

from overlay.app.controller import Reader


def test_reader_starts_without_deps():
    r = Reader(FakeIPC())
    assert r.scorer is None and r.dict_set is None and r.anki is None


def test_apply_deps_injects_and_stops_loading():
    ipc = FakeIPC()
    r = Reader(ipc)
    r._loading = True

    class _Scorer:  # stand-in; not exercised here (no active subtitle)
        pass

    scorer = _Scorer()
    r._apply_deps({"scorer": scorer, "dict_set": None, "anki": None, "mine_cfg": None})
    assert r.scorer is scorer
    assert r._loading is False
    assert any(c and c[0] == "overlay-remove" for c in ipc.commands)  # spinner cleared


def test_runtime_banner_reports_real_worker_count_after_async_deps(capsys, monkeypatch):
    """Regression: the banner printed from run() BEFORE async deps spawned prefetch workers, so it
    always said '0 prefetch worker(s)'. It must now fire from apply_deps with the live count, exactly
    once (a later re-inject must not re-print)."""
    r = Reader(FakeIPC())
    monkeypatch.setattr(r, "start_prefetch", lambda: r._prefetch_threads.extend(("w1", "w2", "w3")))
    r._apply_deps({"scorer": None, "dict_set": None, "anki": None, "mine_cfg": None})
    assert "3 prefetch worker(s)" in capsys.readouterr().out  # real count, not 0
    r._apply_deps({})  # a second injection must not re-announce
    assert "prefetch worker(s)" not in capsys.readouterr().out


def test_prefetch_worker_count_honors_explicit_config_else_auto_by_build(monkeypatch):
    """`[perf].prefetch_workers` > 0 pins the count on both builds (a RAM/coverage knob); 0 auto-sizes
    — min(8, cores-2) free-threaded (render parallelizes), 2 on a GIL build (extra workers only contend)."""
    from types import SimpleNamespace

    from overlay.app import prefetch

    # explicit override wins regardless of build
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: True)
    assert prefetch.prefetch_worker_count(SimpleNamespace(prefetch_workers=3)) == 3
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: False)
    assert prefetch.prefetch_worker_count(SimpleNamespace(prefetch_workers=3)) == 3

    # auto (0): GIL build stays at the low default
    assert (
        prefetch.prefetch_worker_count(SimpleNamespace(prefetch_workers=0))
        == prefetch._AUTO_WORKERS_GIL
    )
    # auto (0): free-threaded uses the flat free-threaded default (no cpu-count arithmetic)
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: True)
    assert (
        prefetch.prefetch_worker_count(SimpleNamespace(prefetch_workers=0))
        == prefetch._AUTO_WORKERS_FREE_THREADED
    )


def test_load_deps_async_marks_loading(monkeypatch):
    import overlay.app.reader_deps as rd

    monkeypatch.setattr(rd, "build_reader_deps", lambda _cfg, **_k: (None, None, None, None))
    r = Reader(FakeIPC())
    r.load_deps_async({})
    assert r._loading is True  # spinner shows until the poll loop injects
