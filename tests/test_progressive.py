"""Progressive startup: reader runs subs-only, then injects deps (coloring/dicts/mining)."""

from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import Future

import pytest
from util import FakeIPC, await_ready, runtime_gateway

from saitenka import otel_metrics
from saitenka.app import mined_seed as mined_seed_lane
from saitenka.app.bindings import SUB_PICKER_MSG
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.tokenize import Token
from saitenka.mpvio.ipc import IPCRequest


def test_reader_starts_without_deps():
    r = Reader(FakeIPC())
    assert r.scorer is None and r.dict_set is None and r.anki is None


@pytest.mark.timeout(5)
def test_demo_annotation_drives_its_broker_terminal_before_the_reader_loop() -> None:
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
    try:
        reader.prepare_subtitle_blocking("猫")

        assert [token.surface for token in reader.tokens] == ["猫"]
    finally:
        reader.close()
        gateway.close()


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


def test_mined_seed_result_publishes_from_the_runtime_lane(monkeypatch):
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    monkeypatch.setattr(mined_seed_lane, "mined_expressions", lambda _anki, _cfg: {"猫"})
    r = Reader(ipc)
    r.anki = object()
    r.mine_cfg = object()
    try:
        r._request_mined_seed()
        await_ready(
            lambda: bool(r.session.mined), "mined seed never published", pump=r._drain_events
        )

        assert r.session.mined == {"猫"}
        assert r.session.mined.generation == 1
        assert r.mined_seed.inflight is False
    finally:
        r.close()
        gateway.close()


def test_mined_seed_result_from_replaced_dependencies_is_rejected(monkeypatch):
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    old_anki = object()
    new_anki = object()
    old_started = threading.Event()
    release = threading.Event()

    def fetch(anki, _cfg):
        if anki is old_anki:
            old_started.set()
            release.wait(1)
            return {"古い"}
        return {"新しい"}

    monkeypatch.setattr(mined_seed_lane, "mined_expressions", fetch)
    r = Reader(ipc)
    r.anki = old_anki
    r.mine_cfg = object()
    try:
        r._request_mined_seed()
        assert old_started.wait(1)
        r.mined_seed.restart()  # what replacing the deck does, through the lane's own verb
        r.anki = new_anki
        r._request_mined_seed()
        await_ready(
            lambda: bool(r.session.mined), "mined seed never published", pump=r._drain_events
        )

        assert r.session.mined == {"新しい"}
        assert r.session.mined.generation == 1
        release.set()
        # Not a wait: drain repeatedly to give a late result the chance to arrive, then prove it
        # did not. A deadline helper would return on the first pass and assert nothing.
        for _ in range(200):
            r._drain_events()
            time.sleep(0.001)

        assert r.session.mined == {"新しい"}
        assert r.session.mined.generation == 1
    finally:
        r.close()
        gateway.close()


def test_mined_seed_retries_after_a_transient_failure(monkeypatch):
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    r = Reader(ipc)
    r.anki = object()
    r.mine_cfg = object()
    attempts = 0

    def fetch(_anki, _cfg):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return None
        return {"猫"}

    monkeypatch.setattr(mined_seed_lane, "mined_expressions", fetch)
    try:
        r._request_mined_seed()
        await_ready(
            lambda: not r.mined_seed.inflight,
            "the failed seed never settled",
            pump=r._drain_events,
        )
        assert r.session.mined == set()

        # The backoff is a named deadline now, so firing it *is* the retry — and asserting it was
        # armed proves the failure path scheduled one rather than silently giving up.
        assert ipc.fire_runtime_timer("lifecycle:mined-seed-retry")
        await_ready(
            lambda: bool(r.session.mined), "mined seed never published", pump=r._drain_events
        )

        assert attempts == 2 and r.session.mined == {"猫"}
    finally:
        r.close()
        gateway.close()


def test_reader_close_cancels_accepted_interaction_jobs(monkeypatch):
    spans = []

    @contextlib.contextmanager
    def traced(name, **attrs):
        spans.append((name, attrs))
        yield None

    monkeypatch.setattr(otel_metrics, "traced", traced)
    r = Reader(FakeIPC())
    r.tip.jobs.begin("tooltip")

    r.close()

    assert spans[-1][0] == "tooltip_request"
    assert spans[-1][1]["outcome"] == "cancelled"


def test_runtime_banner_reports_real_worker_count_after_async_deps(capsys, monkeypatch):
    """Regression: the banner printed from run() BEFORE async deps spawned prefetch workers, so it
    always said '0 prefetch worker(s)'. It must now fire from apply_deps with the live count, exactly
    once (a later re-inject must not re-print)."""
    r = Reader(FakeIPC())
    monkeypatch.setattr(r, "start_prefetch", lambda: setattr(r.prefetch_state, "workers", 3))
    r._apply_deps({"scorer": None, "dict_set": None, "anki": None, "mine_cfg": None})
    assert "3 prefetch worker(s)" in capsys.readouterr().out  # real count, not 0
    r._apply_deps({})  # a second injection must not re-announce
    assert "prefetch worker(s)" not in capsys.readouterr().out


def test_prefetch_worker_count_honors_explicit_config_else_auto_by_build(monkeypatch):
    """`[perf].prefetch_workers` > 0 pins the count on both builds (a RAM/coverage knob); 0 auto-sizes
    — min(8, cores-2) free-threaded (render parallelizes), 2 on a GIL build (extra workers only contend)."""

    from saitenka.app import prefetch
    from saitenka.app.tokenizer import UnidicTokenizer

    def count(configured: int) -> int:
        return prefetch.prefetch_worker_count(UnidicTokenizer(), configured)

    # explicit override wins regardless of build
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: True)
    assert count(3) == 3
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: False)
    assert count(3) == 3

    # auto (0): GIL build stays at the low default
    assert count(0) == prefetch._AUTO_WORKERS_GIL
    # auto (0): free-threaded uses the flat free-threaded default (no cpu-count arithmetic)
    monkeypatch.setattr(prefetch, "gil_disabled", lambda: True)
    assert count(0) == prefetch._AUTO_WORKERS_FREE_THREADED


def test_owned_startup_hint_clears_after_the_first_completed_poll(request):
    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    install_session_reactor(gateway)
    r = Reader(ipc)
    request.addfinalizer(r.close)  # LIFO: the reader goes down before its gateway
    assert ("show-text", "", 1) not in ipc.commands

    assert r.pump() is True
    assert r._interactive_ready is True
    assert ("show-text", "", 1) in ipc.commands

    before = ipc.commands.count(("show-text", "", 1))
    r.pump()
    assert ipc.commands.count(("show-text", "", 1)) == before


def test_first_batch_command_dispatches_before_readiness_clears_the_hint(monkeypatch, request):
    from saitenka.app.session_routes import install_session_reactor

    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    install_session_reactor(gateway)
    reader = Reader(ipc)
    request.addfinalizer(reader.close)  # LIFO: the reader goes down before its gateway
    clear = ("show-text", "", 1)
    observed = []
    monkeypatch.setattr(
        reader,
        "toggle_sub_picker",
        lambda: observed.append(clear in ipc.commands),
    )
    ipc.emit({"event": "client-message", "args": [SUB_PICKER_MSG]})

    assert reader.pump() is True
    assert observed == [False]
    assert clear in ipc.commands


def test_unanswered_async_clear_does_not_delay_the_next_poll(request):
    from saitenka.app.session_routes import install_session_reactor

    class _AsyncFakeIPC(FakeIPC):
        def __init__(self):
            super().__init__()
            self.requests: list[IPCRequest] = []

        def command_async(self, *args, expected_connection_epoch=None):
            del expected_connection_epoch
            request = IPCRequest(len(self.requests), 0, Future())
            self.commands.append(args)
            self.requests.append(request)
            return request

    ipc = _AsyncFakeIPC()
    gateway = runtime_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    install_session_reactor(gateway)
    ipc.requests[0].future.set_result({"error": "success"})
    reader = Reader(ipc)
    request.addfinalizer(reader.close)  # LIFO: the reader goes down before its gateway

    assert reader.pump() is True
    assert ("show-text", "", 1) in ipc.commands
    assert ipc.requests[-1].future.done() is False
    assert reader.pump() is True


def test_load_deps_async_marks_loading(monkeypatch):
    import saitenka.app.reader_deps as rd

    monkeypatch.setattr(rd, "build_reader_deps", lambda _cfg, **_k: (None, None, None, None))
    r = Reader(FakeIPC())
    r.load_deps_async({})
    assert r._loading is True  # spinner shows until the deps-ready deadline injects


def test_a_finished_dep_build_is_injected_by_its_own_deadline(monkeypatch):
    """The build thread hands the value over and *arms* the injection; a tick used to discover it by
    looking. The due event runs on the session turn, which is what makes the hand-off safe."""
    import saitenka.app.reader_deps as rd

    monkeypatch.setattr(rd, "build_reader_deps", lambda _cfg, **_k: (None, None, None, None))
    ipc = FakeIPC()
    r = Reader(ipc)
    applied = []
    monkeypatch.setattr(r, "_apply_deps", applied.append)

    r.load_deps_async({})
    await_ready(lambda: "lifecycle:deps-ready" in ipc.timers, "the build never armed the injection")

    assert applied == []  # arming is not applying
    assert ipc.fire_runtime_timer("lifecycle:deps-ready")
    assert len(applied) == 1

    assert not ipc.fire_runtime_timer("lifecycle:deps-ready")  # one build, one injection
    assert len(applied) == 1


def test_the_value_is_published_before_the_injection_is_armed(monkeypatch):
    """Ordering, and the whole hand-off: arming first would let the due event fire against a
    `_pending_deps` that is still None, and the session would sit loading forever."""
    import saitenka.app.reader_deps as rd

    monkeypatch.setattr(rd, "build_reader_deps", lambda _cfg, **_k: (None, None, None, None))
    ipc = FakeIPC()
    r = Reader(ipc)
    seen: list[object] = []
    original = r.arm_deps_ready
    monkeypatch.setattr(r, "arm_deps_ready", lambda: (seen.append(r._pending_deps), original())[1])

    r.load_deps_async({})
    await_ready(lambda: bool(seen), "the build thread never armed deps-ready")

    assert seen and seen[0] is not None


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
    gateway = runtime_gateway(ipc)
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
    ipc.emit({"event": "client-message", "args": [SUB_PICKER_MSG]})

    assert reader.pump() is True
    assert dispatched == [True]
    assert reader.tokens == [] and reader._sub_pending == "猫"
    assert dictionary.thread_id != threading.get_ident()

    dictionary.release.set()
    assert dictionary.finished.wait(1)
    deadline = time.monotonic() + 1
    while not reader.tokens and time.monotonic() < deadline:
        reader.pump()
        time.sleep(0.001)
    try:
        assert [token.surface for token in reader.tokens] == ["猫"]
        assert reader._sub_pending is None
    finally:
        dictionary.release.set()
        reader.close()
        gateway.close()
