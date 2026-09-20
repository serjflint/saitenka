"""Telemetry never supplies rendering identity or records an unrequested session."""

import hashlib

import pytest
from telemetry_helpers import enable_telemetry
from test_cue_color_timeline import TIMELINE, _coloring, _dialogue, _session, _settle

from saitenka import otel_metrics
from saitenka.app import telemetry


@pytest.mark.parametrize("tracing", [False, True])
@pytest.mark.timeout(5)
def test_late_repeated_cue_identity_is_independent_of_telemetry(tmp_path, monkeypatch, tracing):
    if tracing:
        enable_telemetry(monkeypatch, tmp_path)
    result, ipc, _, _ = _session(tmp_path, monkeypatch, TIMELINE, scorer=_coloring())
    try:
        cue = TIMELINE[-1]
        ipc.set_prop("sub-start", None)
        ipc.set_prop("time-pos", 0)
        ipc.set_prop("sub-text/ass-full", _dialogue(cue))
        ipc.set_prop("sub-text", cue.text)
        _settle(result, ipc)

        ipc.set_prop("time-pos", cue.start + 0.1)
        _settle(result, ipc)

        request = result.graph.cue.draw_request()
        assert request.whole_cue_identity[:2] == (
            hashlib.blake2s(cue.text.encode(), digest_size=16).hexdigest(),
            round(cue.start * 1000),
        )
        assert request.boxes
        if not tracing:
            assert result.graph.subtitle_presentation.color_telemetry.current is None
            assert not ipc.fire_runtime_timer("subtitle:color-deadline")
    finally:
        result.close()
        telemetry.shutdown()


def test_disabled_recording_does_not_import_sdk_or_write_diagnostics(tmp_path, monkeypatch):
    from saitenka.app import option_evidence, player_evidence, query_evidence, render_evidence
    from saitenka.app.config import ReaderOptions

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    real_import = __import__

    def no_sdk(name, *args, **kwargs):
        if name.startswith("opentelemetry"):
            pytest.fail("disabled telemetry imported the SDK")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_sdk)
    monkeypatch.setattr(otel_metrics, "_trace_module", None)
    monkeypatch.setattr(otel_metrics, "_trace_available", None)

    def unexpected_clock():
        pytest.fail("disabled color recording read the clock")

    monkeypatch.setattr(otel_metrics.time, "perf_counter", unexpected_clock)
    otel_metrics.record_cue_arrival("unrecorded")
    otel_metrics.record_color_up("unrecorded")
    option_evidence.record(ReaderOptions())
    geometry = render_evidence.GeometryEvidence()
    geometry.whole_cue({"event": "decision", "reason": "font-access"})
    geometry.timed_osd({"event": "ack"})
    player_evidence.PlayerEvidence().record({"sub-scale": 1.0}, "authored-ass")
    replies = query_evidence.QueryEvidence()
    assert replies.command(lambda *_: {"error": "success"}, "get_property", "sid", epoch=0) == {
        "error": "success"
    }
    operation = otel_metrics.DeferredSpan("runtime_job")
    operation.finish(outcome="succeeded")
    telemetry.shutdown()

    assert render_evidence.registry.snapshot()["owners"] == []
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("variable", "value", "status"),
    [
        ("OTEL_SDK_DISABLED", "true", "not-collected"),
        ("OTEL_TRACES_SAMPLER", "always_off", "partial"),
    ],
)
def test_effective_sdk_policy_cannot_claim_a_complete_zero_census(
    tmp_path, monkeypatch, variable, value, status
):
    from saitenka.app import report
    from saitenka.session import session_id

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv(variable, value)
    enable_telemetry(monkeypatch, tmp_path)
    try:
        operation = otel_metrics.DeferredSpan("subtitle_device_upload")
        operation.finish(outcome="failed")
    finally:
        telemetry.shutdown()

    _, health, *_ = report._metadata_producer(session_id())

    assert health["status"] == status
    if status == "partial":
        assert health["sampled"] is True
    else:
        assert not (tmp_path / "diagnostics").exists()
