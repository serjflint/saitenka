from __future__ import annotations

import threading
import time

from util import FakeIPC, runtime_gateway

from saitenka.app.capabilities import CapabilityProbe, configure_runtime_jobs
from saitenka.app.controller import Reader
from saitenka.app.tokenize import Token
from saitenka.app.tooltip import panel_key
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome


def _await_result(probe: CapabilityProbe) -> None:
    for _ in range(200):
        if probe.value is not None:
            return
        time.sleep(0.001)
    raise AssertionError("capability probe did not publish")


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
    calls = 0

    def run() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            first.wait(1)
            return False
        second.wait(1)
        return True

    probe = CapabilityProbe(run, name="test", ttl=30, retry=1, timeout=5, clock=lambda: clock[0])
    assert probe.request()
    clock[0] = 6.0
    assert probe.request()
    assert not probe.request(force=True)
    second.set()
    _await_result(probe)
    assert probe.value is True and calls == 2

    first.set()
    time.sleep(0.01)
    assert probe.value is True


def test_reader_construction_does_not_run_tts_probe(monkeypatch):
    called = False

    def probe() -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr("saitenka.app.controller.tts_available", probe)
    reader = Reader(FakeIPC())
    try:
        assert called is False
        assert reader._tts_ok is False
    finally:
        reader.close()


def test_late_tts_result_changes_panel_cache_identity(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(
        "saitenka.app.controller.tts_available",
        lambda: release.wait(1) or True,
    )
    reader = Reader(FakeIPC())
    token = Token("猫", "猫", "ネコ", "名詞", 0, 1)
    try:
        before = panel_key(reader, token, "猫")
        reader._apply_capabilities()
        release.set()
        for _ in range(200):
            reader._apply_capabilities()
            if reader._tts_ok:
                break
            time.sleep(0.001)
        after = panel_key(reader, token, "猫")

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

    monkeypatch.setattr("saitenka.app.controller.tts_available", probe)
    ipc = FakeIPC()
    gateway = runtime_gateway(ipc)
    reader = Reader(ipc)
    try:
        reader._apply_capabilities()
        assert finished.wait(1.0)
        assert reader._tts_ok is False

        for _ in range(200):
            reader._drain_events()
            reader._apply_capabilities()
            if reader._tts_ok:
                break
            time.sleep(0.001)

        assert reader._tts_ok is True
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
    calls = {"tts": 0, "anki": 0}

    def make_probe(name: str):
        def probe() -> bool:
            calls[name] += 1
            if calls[name] == 1:
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
