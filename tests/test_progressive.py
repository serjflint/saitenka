"""Progressive startup: reader runs subs-only, then injects deps (coloring/dicts/mining)."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from concurrent.futures import Future

import pytest
from util import FakeIPC

from saitenka import otel_metrics
from saitenka.app.bindings import SUB_PICKER_MSG
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.tokenize import Token
from saitenka.mpvio.ipc import IPCRequest


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


def test_mined_seed_result_publishes_once_on_the_next_tick():
    r = Reader(FakeIPC())
    r._mined_seed_inflight = True
    r._mined_seed_results.put((r._mined_seed_generation, {"猫"}))

    r._apply_pending_mined_seed()
    r._apply_pending_mined_seed()

    assert r._mined == {"猫"}
    assert r._mined_generation == 1
    assert r._mined_seed_inflight is False


def test_mined_seed_result_from_replaced_dependencies_is_rejected():
    r = Reader(FakeIPC())
    r._mined_seed_results.put((r._mined_seed_generation, {"古い"}))
    r._mined_seed_generation += 1

    r._apply_pending_mined_seed()

    assert r._mined == set()
    assert r._mined_generation == 0


def test_mined_seed_retries_after_a_transient_failure(monkeypatch):
    r = Reader(FakeIPC())
    r.anki = object()
    r.mine_cfg = object()
    attempts = 0
    published = threading.Event()

    class ResultQueue:
        def __init__(self) -> None:
            self._queue = queue.Queue()

        def put(self, result) -> None:
            self._queue.put(result)
            published.set()

        def get_nowait(self):
            return self._queue.get_nowait()

    r._mined_seed_results = ResultQueue()

    def fetch(_anki, _cfg):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return None
        return {"猫"}

    monkeypatch.setattr(r._miner, "mined_expressions", fetch)
    r._request_mined_seed()
    assert published.wait(1)
    r._apply_pending_mined_seed()
    assert r._mined == set()

    published.clear()
    r._mined_seed_next_due = 0.0
    r._request_mined_seed()
    assert published.wait(1)
    r._apply_pending_mined_seed()

    assert attempts == 2 and r._mined == {"猫"}


def test_reader_close_cancels_accepted_interaction_jobs(monkeypatch):
    spans = []

    @contextlib.contextmanager
    def traced(name, **attrs):
        spans.append((name, attrs))
        yield None

    monkeypatch.setattr(otel_metrics, "traced", traced)
    r = Reader(FakeIPC())
    r._interaction_jobs.begin("tooltip")

    r.close()

    assert spans[-1][0] == "tooltip_request"
    assert spans[-1][1]["outcome"] == "cancelled"


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

    from saitenka.app import prefetch
    from saitenka.app.tokenizer import UnidicTokenizer

    def fake_reader(prefetch_workers: int) -> SimpleNamespace:
        return SimpleNamespace(prefetch_workers=prefetch_workers, tokenizer=UnidicTokenizer())

    # explicit override wins regardless of build
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: True)
    assert prefetch.prefetch_worker_count(fake_reader(3)) == 3
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: False)
    assert prefetch.prefetch_worker_count(fake_reader(3)) == 3

    # auto (0): GIL build stays at the low default
    assert prefetch.prefetch_worker_count(fake_reader(0)) == prefetch._AUTO_WORKERS_GIL
    # auto (0): free-threaded uses the flat free-threaded default (no cpu-count arithmetic)
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: True)
    assert prefetch.prefetch_worker_count(fake_reader(0)) == prefetch._AUTO_WORKERS_FREE_THREADED


def test_owned_startup_hint_clears_after_the_first_completed_poll():
    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    lease = show_startup_hint(ipc)
    r = Reader(ipc, startup_hint_lease=lease)
    assert ("show-text", "", 1) not in ipc.commands

    assert r.poll_once() is True
    assert r._interactive_ready is True
    assert ("show-text", "", 1) in ipc.commands

    before = ipc.commands.count(("show-text", "", 1))
    r.poll_once()
    assert ipc.commands.count(("show-text", "", 1)) == before


def test_first_batch_command_dispatches_before_readiness_clears_the_hint(monkeypatch):
    from saitenka.app.loading import show_startup_hint

    ipc = FakeIPC()
    reader = Reader(ipc, startup_hint_lease=show_startup_hint(ipc))
    clear = ("show-text", "", 1)
    observed = []
    monkeypatch.setattr(
        reader,
        "toggle_sub_picker",
        lambda: observed.append(clear in ipc.commands),
    )
    ipc.events.append({"event": "client-message", "args": [SUB_PICKER_MSG]})

    assert reader.poll_once() is True
    assert observed == [False]
    assert clear in ipc.commands


def test_unanswered_async_clear_does_not_delay_the_next_poll():
    from saitenka.app.loading import show_startup_hint

    class _AsyncFakeIPC(FakeIPC):
        def __init__(self):
            super().__init__()
            self.requests: list[IPCRequest] = []

        def command_async(self, *args):
            request = IPCRequest(len(self.requests), 0, Future())
            self.commands.append(args)
            self.requests.append(request)
            return request

    ipc = _AsyncFakeIPC()
    lease = show_startup_hint(ipc)
    assert lease is not None
    ipc.requests[0].future.set_result({"error": "success"})
    reader = Reader(ipc, startup_hint_lease=lease)

    assert reader.poll_once() is True
    assert ("show-text", "", 1) in ipc.commands
    assert ipc.requests[-1].future.done() is False
    assert reader.poll_once() is True


def test_load_deps_async_marks_loading(monkeypatch):
    import saitenka.app.reader_deps as rd

    monkeypatch.setattr(rd, "build_reader_deps", lambda _cfg, **_k: (None, None, None, None))
    r = Reader(FakeIPC())
    r.load_deps_async({})
    assert r._loading is True  # spinner shows until the poll loop injects


@pytest.mark.timeout(5)
def test_dependency_publication_never_runs_attestation_on_the_reader_tick(monkeypatch):
    class _Tokenizer:
        name = "test"

        def tokenize(self, line, **_kwargs):
            return [Token(line, line, line, "名詞", 0, len(line))]

        def merge_dict_compounds(self, tokens, exists):
            exists(tuple(token.surface for token in tokens))
            return tokens

    class _BlockingDictionary:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()
            self.thread_id = None

        def terms_exist(self, _forms):
            self.thread_id = threading.get_ident()
            self.started.set()
            assert self.release.wait(2)
            self.finished.set()
            return set()

    ipc = FakeIPC()
    ipc.props.update({"sub-text": "猫", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    reader = Reader(ipc)
    reader.renderer = NullRenderer()
    reader.tokenizer = _Tokenizer()
    reader._enable_async_annotation()
    reader.set_subtitle("猫")
    dictionary = _BlockingDictionary()
    dispatched = []
    monkeypatch.setattr(reader, "toggle_sub_picker", lambda: dispatched.append(True))

    reader._apply_deps({"dict_set": dictionary})
    assert dictionary.started.wait(1)
    ipc.events.append({"event": "client-message", "args": [SUB_PICKER_MSG]})

    assert reader.poll_once() is True
    assert dispatched == [True]
    assert reader.tokens == [] and reader._sub_pending == "猫"
    assert dictionary.thread_id != threading.get_ident()

    dictionary.release.set()
    assert dictionary.finished.wait(1)
    deadline = time.monotonic() + 1
    while not reader.tokens and time.monotonic() < deadline:
        reader.poll_once()
        time.sleep(0.001)
    try:
        assert [token.surface for token in reader.tokens] == ["猫"]
        assert reader._sub_pending is None
    finally:
        dictionary.release.set()
        reader.close()
