from __future__ import annotations

import threading
import time

from session_builder import build_session
from util import FakeIPC, await_ready, runtime_gateway

from saitenka.app.capabilities import CapabilityProbe, configure_runtime_jobs
from saitenka.app.features.tooltip.tooltip import panel_key
from saitenka.app.tokenize import Token
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome


def _await_result(probe: CapabilityProbe) -> None:
    await_ready(lambda: probe.value is not None, "capability probe did not publish")


def test_capability_probe_deduplicates_and_publishes_snapshot():
    release = threading.Event()
    calls = 0

    def blocked() -> bool:
        nonlocal calls
        calls += 1
        release.wait(1)
        return True

    probe = CapabilityProbe(blocked, name="test", ttl=10, retry=1)
    assert probe.request() is True
    assert probe.request() is False
    release.set()
    _await_result(probe)

    assert probe.value is True and calls == 1
    assert probe.request() is False


def test_capability_close_rejects_late_result():
    release = threading.Event()
    probe = CapabilityProbe(
        lambda: release.wait(1) or True,
        name="test",
        ttl=10,
        retry=1,
    )
    probe.request()
    probe.close()
    release.set()
    time.sleep(0.01)

    assert probe.value is None


def test_wedged_probe_is_replaced_once_and_late_result_is_rejected():
    clock = [0.0]
    first = threading.Event()
    second = threading.Event()
    wedged = threading.Event()
    calls = 0

    def run() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            wedged.set()
            # Bounds a WEDGE, not a timeout: expiring on its own would unwedge the probe and
            # publish False, which reads as a rejected-late-result bug.
            first.wait(30)
            return False
        second.wait(30)
        return True

    probe = CapabilityProbe(run, name="test", ttl=30, retry=1, timeout=5, clock=lambda: clock[0])
    assert probe.request()
    # Ordering the two threads: the clock is fake, so without this the replacement is spawned
    # microseconds later and can reach `run` first, taking the wedge branch meant for its
    # predecessor. `calls` is then read from one thread at a time, too.
    assert wedged.wait(5), "the first probe never reached the callable it is supposed to wedge in"
    clock[0] = 6.0
    assert probe.request()
    assert not probe.request(force=True)
    second.set()
    _await_result(probe)
    assert probe.value is True and calls == 2

    first.set()
    # Not a wait: let the unwedged first probe finish and prove its stale result is still rejected.
    time.sleep(0.01)
    assert probe.value is True


def test_reader_construction_does_not_run_tts_probe(monkeypatch):
    called = False

    def probe() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr("saitenka.app.session.controller.tts_available", probe)
    reader = build_session(FakeIPC())
    try:
        assert called is False
        assert reader.tooltip_controller.panel_style.speak_button is False
    finally:
        reader.close()


def test_late_tts_result_changes_panel_cache_identity(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(
        "saitenka.app.session.controller.tts_available",
        lambda: release.wait(1) or True,
    )
    reader = build_session(FakeIPC())
    token = Token("猫", "猫", "ネコ", "名詞", 0, 1)
    try:
        before = panel_key(reader.tooltip_controller.panel_ports, token, "猫")
        reader._apply_capabilities()
        release.set()
        await_ready(
            lambda: reader.tooltip_controller.panel_style.speak_button,
            "tts probe never published",
            pump=reader._apply_capabilities,
        )
        after = panel_key(reader.tooltip_controller.panel_ports, token, "猫")

        assert before.tts_ok is False
        assert after.tts_ok is True
        assert before != after
    finally:
        reader.close()


def test_runtime_capability_completion_changes_reader_only_after_event_delivery(monkeypatch):
    finished = threading.Event()

    def probe() -> bool:
        finished.set()
        return True

    monkeypatch.setattr("saitenka.app.session.controller.tts_available", probe)
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = build_session(ipc)
    try:
        reader._apply_capabilities()
        assert finished.wait(1.0)
        assert reader.tooltip_controller.panel_style.speak_button is False

        def deliver() -> None:
            reader._drain_events()
            reader._apply_capabilities()

        await_ready(
            lambda: reader.tooltip_controller.panel_style.speak_button,
            "capability event never reached the reader",
            pump=deliver,
        )

        assert reader.tooltip_controller.panel_style.speak_button is True
    finally:
        reader.close()
        gateway.close()


#: How long the first probe stays wedged before letting go on its own. Short on purpose — the
#: replacement's result is only accepted once the call it replaced has finished.
_WEDGE_TIMEOUT = 1.0
#: Bounds a hang, never a race: far past what the work needs, because a test that passes only on an
#: idle machine is the defect this replaced.
_REPLACEMENT_TIMEOUT = 10.0


def test_runtime_lane_can_replace_both_wedged_capability_probes() -> None:
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    submit = configure_runtime_jobs(ipc)
    assert submit is not None
    clock = [0.0]
    releases = {"tts": threading.Event(), "anki": threading.Event()}
    started = {"tts": threading.Event(), "anki": threading.Event()}
    calls = {"tts": 0, "anki": 0}

    def make_probe(name: str):
        def probe() -> bool:
            calls[name] += 1
            if calls[name] == 1:
                started[name].set()
                # Self-releases, and must: the wedge has to end for the *replacement's* terminal to
                # be accepted, so holding it for the whole assertion deadlocks the very thing being
                # measured. Retry timing runs off the injected clock, so this cannot re-fire the
                # probe on wall time — it only bounds how long the first call blocks.
                releases[name].wait(_WEDGE_TIMEOUT)
                return False
            return True

        return CapabilityProbe(
            probe,
            name=name,
            ttl=30.0,
            retry=1.0,
            timeout=5.0,
            clock=lambda: clock[0],
            submit=submit,
        )

    probes = tuple(make_probe(name) for name in ("tts", "anki"))
    try:
        assert all(probe.request() for probe in probes)
        # Wait for the probes to actually be RUNNING before replacing them. Submitting is not
        # starting: the lane admits the job, and under load a worker may not pick it up for a while.
        # If the replacement is submitted first, a worker can run IT first — and this fake decides
        # "am I the wedge?" from a call counter, so the replacement would take the wedge branch,
        # return False, and the generation fence would correctly accept that False. The runtime
        # promises no ordering between a superseded job and its replacement, so the test must not
        # assume one. (This was a real flake: ~4 in 12 suite runs, only under load.)
        for name, running in started.items():
            assert running.wait(_WEDGE_TIMEOUT), f"{name} probe never started"
        clock[0] = 6.0
        assert all(probe.request() for probe in probes)
        # A deadline, not a fixed number of 1 ms sleeps: the replacement runs on a lane thread, and
        # under `-n auto` the old 200 ms budget expired before it was ever scheduled. The wait is a
        # timeout guarding a hang, so it may be generous — what it must not be is a race.
        deadline = time.monotonic() + _REPLACEMENT_TIMEOUT
        while time.monotonic() < deadline:
            ipc.drain_events()
            if all(probe.value is True for probe in probes):
                break
            time.sleep(0.001)

        assert calls == {"tts": 2, "anki": 2}
        assert all(probe.value is True for probe in probes)
    finally:
        for probe in probes:
            probe.close()
        for release in releases.values():
            release.set()
        gateway.close()


def test_runtime_admission_failure_preserves_last_known_capability() -> None:
    clock = [0.0]
    outcomes = [
        (EffectOutcome.SUCCEEDED, True, None),
        (EffectOutcome.REJECTED, None, EffectError.OVERLOADED),
        (EffectOutcome.SUCCEEDED, True, None),
    ]

    def submit(*, owner, identity, lane, request, on_finished) -> bool:
        del lane, request
        outcome, result, error = outcomes.pop(0)
        on_finished(
            EffectFinished(EffectId(3 - len(outcomes)), owner, identity, outcome, result, error)
        )
        return outcome is EffectOutcome.SUCCEEDED

    probe = CapabilityProbe(
        lambda: True,
        name="test",
        ttl=30.0,
        retry=1.0,
        clock=lambda: clock[0],
        submit=submit,
    )
    assert probe.request()
    assert probe.value is True

    assert not probe.request(force=True)
    assert probe.value is True
    assert not probe.request()
    clock[0] = 1.0
    assert probe.request()
    assert probe.value is True
