"""The render-executor policy: threads on a free-threaded build, processes on a GIL build."""

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from overlay import parallel


def test_free_threaded_build_picks_threads(monkeypatch):
    monkeypatch.setattr(parallel, "is_free_threaded", lambda: True)
    with parallel.pick_executor(4) as ex:
        assert isinstance(ex, ThreadPoolExecutor)  # shared memory, zero-copy — no IPC on FT


def test_gil_build_falls_back_to_processes(monkeypatch):
    monkeypatch.setattr(parallel, "is_free_threaded", lambda: False)
    with parallel.pick_executor(4) as ex:
        assert isinstance(ex, ProcessPoolExecutor)  # threads would serialise on the GIL


def test_is_free_threaded_matches_the_interpreter():
    import sys

    probe = getattr(sys, "_is_gil_enabled", None)
    expected = probe() is False if probe is not None else False
    assert parallel.is_free_threaded() is expected


def test_the_chosen_executor_actually_runs_work(monkeypatch):
    # On the free-threaded test interpreter this exercises the real thread path end-to-end.
    monkeypatch.setattr(parallel, "is_free_threaded", lambda: True)
    with parallel.pick_executor(2) as ex:
        assert sorted(ex.map(lambda n: n * n, [1, 2, 3])) == [1, 4, 9]
