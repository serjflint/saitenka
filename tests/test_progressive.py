"""Progressive startup: reader runs subs-only, then injects deps (coloring/dicts/mining)."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future

import pytest
from saitenka_card import MineConfig
from saitenka_tokenize.japanese import Token
from util import FakeIPC, await_ready, bare_gateway, session_gateway

import saitenka.app.features.mining.mined_seed as mined_seed_lane
from saitenka.app.bindings import SUB_PICKER_MSG
from saitenka.app.config import PerfOptions, ReaderOptions
from saitenka.app.features.mining.mining_controller import MiningIdentity, MiningSpec, MiningTarget
from saitenka.app.features.profiles.dependencies import DependencyBundle
from saitenka.app.logsetup import CONSOLE_LOGGER_NAME
from saitenka.app.session.factory import SessionServices
from saitenka.app.subtitle_render import NullRenderer
from saitenka.mpvio.ipc import IPCRequest


def test_reader_starts_without_deps(make_session):
    r = make_session(FakeIPC())
    assert (
        r.graph.profile.scorer is None
        and r.graph.profile.profile.dict_set is None
        and not r.graph.mining.configured
    )


@pytest.mark.timeout(5)
def test_demo_annotation_drives_its_broker_terminal_before_the_reader_loop(
    request, make_session
) -> None:
    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    request.addfinalizer(gateway.close)
    assert ipc.commands == []
    reader = make_session(ipc)
    try:
        reader.entry.runtime.prepare_cue("猫")

        assert [
            token.surface for token in reader.graph.subtitle_presentation.cue.current.tokens
        ] == ["猫"]
    finally:
        reader.close()


@pytest.mark.parametrize("has_scorer", [False, True])
def test_apply_deps_injects_and_stops_loading(make_session, has_scorer):
    from saitenka.app.overlay_ids import OverlayId

    ipc = FakeIPC()
    r = make_session(ipc)
    r.graph.profile.begin_loading()
    scorer = object() if has_scorer else None

    r.graph.profile.accept(DependencyBundle(r.graph.profile.identity, scorer=scorer))

    assert r.graph.profile.scorer is scorer
    assert r.graph.profile.loading is False
    assert ("overlay-remove", OverlayId.LOADING) in ipc.commands


@pytest.mark.timeout(5)
@pytest.mark.parametrize("transient_failure", [False, True])
def test_mined_seed_result_publishes_from_the_runtime_lane(
    monkeypatch, make_session, transient_failure
):
    from saitenka.app.features.mining.mining_controller import SeedStatus

    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    attempts = 0

    def fetch(_anki, _cfg):
        nonlocal attempts
        attempts += 1
        return None if transient_failure and attempts == 1 else {"猫"}

    monkeypatch.setattr(mined_seed_lane, "mined_expressions", fetch)
    r = make_session(ipc, services=SessionServices(anki=object(), mining=MineConfig()))
    try:
        generation = r.graph.mining.index_snapshot().generation
        r.graph.mining.request_seed()
        if transient_failure:
            await_ready(
                lambda: r.graph.mining.index_snapshot().seed_status is SeedStatus.DEGRADED,
                "the failed seed never settled",
                pump=r.pump,
            )
            assert r.graph.mining.index_snapshot().values == set()
            assert ipc.fire_runtime_timer("lifecycle:mined-seed-retry")
        await_ready(
            lambda: bool(r.graph.mining.index_snapshot()),
            "mined seed never published",
            pump=r.pump,
        )

        snapshot = r.graph.mining.index_snapshot()
        assert snapshot.values == {"猫"}
        assert snapshot.generation > generation
        assert snapshot.seed_status is SeedStatus.READY
        assert attempts == (2 if transient_failure else 1)
    finally:
        r.close()
        gateway.close()


@pytest.mark.timeout(5)
def test_mined_seed_result_from_replaced_dependencies_is_rejected(monkeypatch, make_session):
    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    old_anki = object()
    new_anki = object()
    old_started = threading.Event()
    release = threading.Event()
    old_finished = threading.Event()
    submit = ipc.submit_runtime_job

    def submit_seed(**job):
        if job["lane"] == "mined-seed" and job["request"].anki is old_anki:
            finish = job["on_finished"]

            def finish_old(completion):
                assert completion.result == {"古い"}
                finish(completion)
                old_finished.set()

            job["on_finished"] = finish_old
        return submit(**job)

    monkeypatch.setattr(ipc, "submit_runtime_job", submit_seed)

    def fetch(anki, _cfg):
        if anki is old_anki:
            old_started.set()
            release.wait()
            return {"古い"}
        return {"新しい"}

    monkeypatch.setattr(mined_seed_lane, "mined_expressions", fetch)
    config = MineConfig()
    r = make_session(ipc, services=SessionServices(anki=old_anki, mining=config))
    try:
        r.graph.mining.request_seed()
        assert old_started.wait(1)
        identity = MiningIdentity("replacement", 1)
        r.graph.mining.select_mining_spec(
            MiningSpec(identity, {"deck": config.deck, "model": config.model})
        )
        r.graph.mining.publish_mining_target(MiningTarget(identity, new_anki, config))
        r.graph.mining.request_seed()
        await_ready(
            lambda: bool(r.graph.mining.index_snapshot()),
            "mined seed never published",
            pump=r.pump,
        )

        current = r.graph.mining.index_snapshot()
        assert current.values == {"新しい"}
        release.set()
        await_ready(old_finished.is_set, "old seed completion was not consumed", pump=r.pump)

        assert r.graph.mining.index_snapshot() == current
    finally:
        release.set()
        r.close()
        gateway.close()


def test_reader_close_cancels_accepted_interaction_jobs(make_session, diagnostic_trace):
    r = make_session(FakeIPC())
    r.graph.tooltip.surface_state().jobs.begin("tooltip")

    r.close()

    events, analysis = diagnostic_trace()
    terminal = [event["args"] for event in events if event["name"] == "tooltip_request"]
    assert terminal[-1]["outcome"] == "cancelled"
    assert analysis["interaction_latency"]["tooltip_request"]["outcomes"] == {"cancelled": 1}


def test_runtime_banner_reports_real_worker_count_after_async_deps(caplog, make_session):
    """Regression: the banner announced from run() BEFORE async deps spawned prefetch workers, so it
    always said '0 prefetch worker(s)'. It must now fire from apply_deps with the live count, exactly
    once (a later re-inject must not re-announce).

    Read off the record, not the terminal: which stream a user-facing line reaches is logsetup's
    (tests/test_logsetup.py), and a SessionController built without `configure_logging` has no handler at all.
    """
    caplog.set_level(logging.INFO, logger=CONSOLE_LOGGER_NAME)
    dictionaries = object()
    ipc = FakeIPC()
    gateway = session_gateway(ipc)
    r = make_session(
        ipc,
        services=SessionServices(dictionaries=dictionaries),
        options=ReaderOptions(
            prefetch=True,
            perf=PerfOptions(prefetch_workers=3, prefetch_lookahead=1),
        ),
    )
    try:
        r.graph.profile.accept(
            DependencyBundle(r.graph.profile.identity, dictionaries=dictionaries)
        )
        assert "3 prefetch worker(s)" in caplog.text  # real count, not 0
        caplog.clear()
        r.graph.profile.accept(DependencyBundle(r.graph.profile.identity))
        assert "prefetch worker(s)" not in caplog.text
    finally:
        r.close()
        gateway.close()


@pytest.mark.parametrize(
    ("free_threaded", "configured", "expected"),
    [(True, 3, 3), (False, 3, 3), (False, 0, 2), (True, 0, 4)],
)
def test_prefetch_worker_count_honors_explicit_config_else_auto_by_build(
    monkeypatch, free_threaded, configured, expected
):
    from saitenka_tokenize.registry import UnidicTokenizer

    from saitenka.app.features.tooltip import prefetch

    monkeypatch.setattr(prefetch, "gil_disabled", lambda: free_threaded)

    assert prefetch.prefetch_worker_count(UnidicTokenizer(), configured) == expected


@pytest.mark.parametrize(
    ("has_command", "connected_at", "since_ipc"), [(False, 1.0, 1000.0), (True, None, None)]
)
def test_owned_startup_hint_clears_after_the_first_completed_turn(
    request, make_session, diagnostic_trace, monkeypatch, has_command, connected_at, since_ipc
):
    from types import SimpleNamespace

    from saitenka.app.session import controller
    from saitenka.app.session.routes import install_session_reactor

    ipc = FakeIPC()
    ipc.connected_at = connected_at
    monkeypatch.setattr(controller, "time", SimpleNamespace(monotonic=lambda: 2.0))
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)
    install_session_reactor(gateway)
    reader = make_session(ipc)
    request.addfinalizer(reader.close)
    reader.start()
    clear = ("show-text", "", 1)
    assert clear not in ipc.commands
    observed = []
    monkeypatch.setattr(
        reader.graph.picker,
        "open",
        lambda *_args, **_kwargs: observed.append(clear in ipc.commands),
    )
    if has_command:
        ipc.emit({"event": "client-message", "args": [SUB_PICKER_MSG]})

    assert reader.pump() is True
    assert observed == ([False] if has_command else [])
    assert ipc.commands.count(clear) == 1

    reader.pump()
    assert ipc.commands.count(clear) == 1
    events, analysis = diagnostic_trace()
    ready = [event for event in events if event["name"] == "startup.interactive_ready"]
    assert len(ready) == 1 and ready[0]["args"].get("since_ipc_ms") == since_ipc
    assert any(row["name"] == "startup.interactive_ready" for row in analysis["startup"])


@pytest.mark.timeout(5)
def test_unanswered_async_clear_does_not_delay_the_next_poll(request, make_session):
    from saitenka.app.session.routes import install_session_reactor

    class _AsyncFakeIPC(FakeIPC):
        def __init__(self):
            super().__init__()
            self.requests: list[IPCRequest] = []

        def command_async(self, *args, expected_connection_epoch=None):
            del expected_connection_epoch
            request = IPCRequest(len(self.requests), 0, Future())
            self.command(*args)
            self.requests.append(request)
            return request

    ipc = _AsyncFakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    gateway = bare_gateway(ipc)
    request.addfinalizer(gateway.close)  # owns threads; a leak here exhausts the pool at -n auto
    install_session_reactor(gateway)
    ipc.requests[0].future.set_result({"error": "success"})
    reader = make_session(ipc)
    request.addfinalizer(reader.close)  # LIFO: the reader goes down before its gateway
    reader.start()

    assert reader.pump() is True
    assert ("show-text", "", 1) in ipc.commands
    assert ipc.requests[-1].future.done() is False
    assert reader.pump() is True


@pytest.mark.timeout(5)
@pytest.mark.parametrize("has_scorer", [False, True])
def test_a_finished_dep_build_is_injected_by_its_own_deadline(
    monkeypatch, make_session, has_scorer
):
    import saitenka.app.features.profiles.dependencies as rd
    from saitenka.app.lifecycle_timers import LifecycleTimerKind, LifecycleTimers

    build = Future()
    armed = threading.Event()
    resume = threading.Event()
    finished = threading.Event()
    scorer = object() if has_scorer else None
    monkeypatch.setattr(rd, "build_reader_deps", lambda _cfg, **_k: build.result(timeout=2))
    ipc = FakeIPC()
    r = make_session(ipc)
    schedule = LifecycleTimers.schedule

    def pause_after_arming(timers, kind, *args, **kwargs):
        accepted = schedule(timers, kind, *args, **kwargs)
        if kind is LifecycleTimerKind.DEPS_READY and not armed.is_set():
            try:
                armed.set()
                resume.wait()
            finally:
                finished.set()
        return accepted

    monkeypatch.setattr(LifecycleTimers, "schedule", pause_after_arming)
    try:
        r.graph.profile.load({})
        assert r.graph.profile.loading is True
        build.set_result((scorer, None, None, None))
        assert armed.wait(1)
        assert (r.graph.profile.scorer, r.graph.profile.loading) == (None, True)

        assert ipc.fire_runtime_timer("lifecycle:deps-ready")

        assert (r.graph.profile.scorer, r.graph.profile.loading) == (scorer, False)
        assert not ipc.fire_runtime_timer("lifecycle:deps-ready")
    finally:
        if not build.done():
            build.set_result((scorer, None, None, None))
        resume.set()
        assert finished.wait(1)


@pytest.mark.timeout(5)
def test_dependency_publication_never_runs_attestation_on_the_reader_tick(
    monkeypatch, make_session
):
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

        def terms_exist(self, _forms):
            self.started.set()
            self.release.wait()
            return set()

    ipc = FakeIPC()
    ipc.props.update({"sub-text": "猫", "sid": 1, "sub-start": 1.0, "sub-end": 2.0})
    gateway = session_gateway(ipc)
    reader = make_session(ipc)
    dictionary = _BlockingDictionary()
    try:
        reader.graph.subtitle_presentation.renderer = NullRenderer()
        reader.graph.profile.profile.use_tokenizer(_Tokenizer())
        reader.graph.profile_integration.enable_async_annotation()
        reader.graph.playback.observe("sub-text", "猫")
        reader.graph.cue.settle()
        dispatched = []
        monkeypatch.setattr(
            reader.graph.picker,
            "open",
            lambda *_args, **_kwargs: dispatched.append(True),
        )

        reader.graph.profile.accept(
            DependencyBundle(reader.graph.profile.identity, dictionaries=dictionary)
        )
        assert dictionary.started.wait(1)
        ipc.emit({"event": "client-message", "args": [SUB_PICKER_MSG]})

        assert reader.pump() is True
        assert dispatched == [True]
        assert (
            reader.graph.subtitle_presentation.cue.current.tokens == []
            and reader.graph.annotation.view.pending_text == "猫"
        )

        dictionary.release.set()
        await_ready(
            lambda: bool(reader.graph.subtitle_presentation.cue.current.tokens),
            "attested cue never published",
            pump=reader.pump,
        )
        assert [
            token.surface for token in reader.graph.subtitle_presentation.cue.current.tokens
        ] == ["猫"]
        assert reader.graph.annotation.view.pending_text is None
    finally:
        dictionary.release.set()
        reader.close()
        gateway.close()
