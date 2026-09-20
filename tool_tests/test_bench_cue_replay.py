"""The replay report preserves failed attempts and partial runs."""

import importlib.util
import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from saitenka.app.features.tooltip.prefetch import PrefetchSnapshot

_spec = importlib.util.spec_from_file_location(
    "bench_cue_replay", Path(__file__).resolve().parents[1] / "examples/bench_cue_replay.py"
)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


def test_incomplete_attempts_remain_in_latency_denominator():
    result = bench.summarize(
        [
            {"status": "complete", "accounting": {"complete_ms": 25}},
            {"status": "no-acknowledgment"},
            {"status": "superseded"},
            {},
        ]
    )

    assert result["attempts"] == 4
    assert result["outcomes"] == {
        "complete": 1,
        "no-acknowledgment": 1,
        "superseded": 1,
        "interrupted": 1,
    }
    assert result["complete_ack_p95_ms_among_completed"] == 25
    assert result["budget_qualification"] is False


def test_benchmark_dictionary_supports_production_cache_signature_and_gauges():
    from live_harness import MiniDS

    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.render_cache import dict_set_signature

    benchmark = MiniDS()
    empty = DictionarySet(dicts=[])

    assert dict_set_signature(benchmark) == dict_set_signature(empty)
    assert benchmark.decoded_entry_count() == empty.decoded_entry_count()


def test_partial_receipt_survives_failure_after_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(bench.time, "monotonic", lambda: 1.0)
    receipt = bench.Receipt(tmp_path, {"source": "auto"})
    receipt.data["attempts"].append({"index": 0, "status": "interrupted"})

    receipt.phase("replay-start")

    saved = json.loads((tmp_path / "result.json").read_text())
    assert saved["status"] == "running"
    assert saved["phases"][0]["name"] == "replay-start"
    assert saved["summary"]["outcomes"] == {"interrupted": 1}


def test_forward_backward_workload_returns_to_its_start():
    steps = bench.navigation_steps("back-forth", 3)

    assert steps == [1, -1, 1, -1, 1, -1]
    assert bench.navigation_steps("replay", 3) == [0, 0, 0]


@pytest.mark.parametrize(("succeeded", "failed"), [(0, 0), (0, 1), (1, 1)])
def test_prefetch_failure_or_missing_success_cannot_qualify_runner_readiness(succeeded, failed):
    snapshot = PrefetchSnapshot(1, 0, 0, 1, 0, False, succeeded, failed)

    with pytest.raises(RuntimeError, match="prefetch did not complete successfully"):
        bench.require_prefetch_success(snapshot)


def test_prefetch_failure_saves_counts_before_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(bench.time, "monotonic", lambda: 1.0)
    snapshot = PrefetchSnapshot(1, 0, 0, 1, 0, False, 0, 1)
    session = SimpleNamespace(
        graph=SimpleNamespace(tooltip=SimpleNamespace(prefetch_snapshot=snapshot))
    )
    receipt = bench.Receipt(tmp_path, {})

    with pytest.raises(RuntimeError, match="prefetch did not complete successfully"):
        bench.await_prefetch(session, receipt)

    saved = json.loads((tmp_path / "result.json").read_text())
    assert saved["phases"][-1]["prefetch"]["failed"] == 1
    assert saved["phases"][-1]["prefetch"]["succeeded"] == 0


def test_nonexecutable_player_is_rejected_instead_of_using_path_fallback(tmp_path, capsys):
    media = tmp_path / "media.mkv"
    subtitles = tmp_path / "subtitles.ass"
    player = tmp_path / "mpv"
    for path in (media, subtitles, player):
        path.touch()
    player.chmod(0o600)

    with pytest.raises(SystemExit) as raised:
        bench.main(
            [
                "--media",
                str(media),
                "--subtitles",
                str(subtitles),
                "--mpv",
                str(player),
                "--start",
                "1",
            ]
        )

    assert raised.value.code == 2
    assert "mpv is not executable" in capsys.readouterr().err


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="Unix profiling deadline")
def test_operation_deadline_escapes_recoverable_application_errors():
    previous = signal.getsignal(signal.SIGALRM)

    def interrupt_application():
        try:
            signal.raise_signal(signal.SIGALRM)
        except Exception:
            pytest.fail("the benchmark deadline became a recoverable application error")

    with bench.operation_deadline(0), pytest.raises(bench.ReplayTimeout):
        interrupt_application()

    assert signal.getsignal(signal.SIGALRM) == previous
