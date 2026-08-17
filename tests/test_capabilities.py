from __future__ import annotations

import threading
import time

from util import FakeIPC

from saitenka.app.capabilities import CapabilityProbe
from saitenka.app.controller import Reader
from saitenka.app.tokenize import Token
from saitenka.app.tooltip import panel_key


def _await_result(probe: CapabilityProbe) -> None:
    for _ in range(200):
        if probe.apply():
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

    assert probe.apply() is False
    assert probe.value is None


def test_wedged_probe_is_replaced_once_and_late_result_is_rejected():
    clock = [0.0]
    first = threading.Event()
    calls = 0

    def run() -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            first.wait(1)
            return False
        return True

    probe = CapabilityProbe(run, name="test", ttl=30, retry=1, timeout=5, clock=lambda: clock[0])
    assert probe.request()
    clock[0] = 6.0
    assert probe.request()
    assert not probe.request(force=True)
    _await_result(probe)
    assert probe.value is True and calls == 2

    first.set()
    time.sleep(0.01)
    assert probe.apply() is False
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
