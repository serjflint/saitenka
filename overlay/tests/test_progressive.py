"""Progressive startup: reader runs subs-only, then injects deps (coloring/dicts/mining)."""

from __future__ import annotations

from overlay.app.controller import Reader
from util import FakeIPC


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


def test_load_deps_async_marks_loading(monkeypatch):
    import overlay.app.reader_deps as rd

    monkeypatch.setattr(rd, "build_reader_deps", lambda cfg, **k: (None, None, None, None))
    r = Reader(FakeIPC())
    r.load_deps_async({})
    assert r._loading is True  # spinner shows until the poll loop injects
