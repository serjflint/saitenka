from __future__ import annotations

import json
import operator
import os
from pathlib import Path

import native_subtitle_integration_benchmark as benchmark
import pytest
from native_subtitle_integration_benchmark import evaluate, load_manifest, summarize_trials


def report() -> dict:
    return {
        "schema": 1,
        "event_count": 101,
        "simultaneous_frame_workloads": [
            {
                "active_events": count,
                "eligible_tokens": count,
                "found_tokens": count,
                "prepare_ms": 1.0,
                "render_ms": 1.0,
                "extract_ms": 1.0,
            }
            for count in (1, 2, 4, 64)
        ],
        "interaction_clock": "thread_time",
        "interaction_p99_ms": 1.0,
        "interaction_cpu_p99_ms": 1.0,
        "interaction_cpu_delta_p99_ms": 0.5,
        "interaction_wall_delta_p99_ms": 0.75,
        "ready_before_presentation_ratio": 100 / 101,
        "ready_before_presented": 100,
        "geometry_apply_count": 100,
        "hit_test_count": 101,
        # Every presented cue, including the cold one whose geometry lands after presentation: the
        # tooltip opens later than `_present` samples, so focus tracks presentations, not readiness.
        "focus_draw_count": 101,
        "tooltip_open_count": 101,
        "tooltip_scroll_count": 101,
        "retained_rss_growth_mib": 32.0,
        "result_cache_entries": 3,
        "prefetch_cache_entries": 3,
        "presented": 101,
        "completed": 101,
        "failures": 0,
        "last_error": None,
        "superseded": 0,
        "prefetch_dropped": 0,
        "cadence_misses": 0,
        "source_clear_current": False,
        "source_clear_hit_count": 0,
        "profile_switch_cache_entries": 0,
        "close_completed": True,
    }


def manifest() -> dict:
    return {
        "event_count": 101,
        "simultaneous_event_counts": [1, 2, 4, 64],
        "trials": 3,
        "cache_max": 3,
        "interaction_clock": "thread_time",
        "presentation_interval_ms": 0.0,
        "budgets": {
            "interaction_cpu_p99_ms": 16.67,
            "interaction_wall_p99_ms": 16.67,
            "interaction_cpu_delta_p99_ms": 2.0,
            "interaction_wall_delta_p99_ms": 16.67,
            "ready_before_presentation_ratio": 0.99,
            "retained_rss_growth_mib": 256.0,
        },
    }


def test_budget_oracle_accepts_locked_boundary() -> None:
    assert evaluate(report(), manifest())


def test_the_locked_fixture_satisfies_every_named_clause() -> None:
    assert all(benchmark._functional_checks(report(), manifest()).values())
    assert all(benchmark._performance_checks(report(), manifest()).values())


def test_a_broken_clause_is_named_and_still_fails_the_oracle() -> None:
    broken = report()
    broken["tooltip_open_count"] = broken["presented"] - 1

    checks = benchmark._functional_checks(broken, manifest())

    assert [name for name, ok in checks.items() if not ok] == ["tooltip_opened_every_presentation"]
    assert not benchmark._functional_passes(broken, manifest()), "naming must not weaken the oracle"


def test_a_foreign_schema_reports_a_failure_rather_than_raising() -> None:
    """The guard short-circuits, as the original `and` chain did. Evaluating the clauses behind it
    subscripts keys another schema need not carry, turning a recordable failure into a dead run."""
    foreign = {"schema": 7}

    assert benchmark._functional_checks(foreign, manifest()) == {"schema": False}
    assert not benchmark._functional_passes(foreign, manifest())


def test_summary_names_the_failing_budget_not_only_the_count() -> None:
    slow = report()
    slow["interaction_cpu_delta_p99_ms"] = manifest()["budgets"]["interaction_cpu_delta_p99_ms"] + 1

    summary = benchmark._summarize(
        benchmark.summarize_trials([slow, slow, slow], manifest()), manifest(), Path("report.json")
    )

    # One of the three budgets the printed numbers do not show, so a count alone cannot explain it.
    assert "interaction_cpu_delta_p99_ms" in summary
    assert summary.startswith("FAIL")


def test_summary_leaves_the_sample_arrays_to_the_artifact() -> None:
    """Negative control: the fixture carries the arrays, so a summary that dumped the report fails
    this. Asserting their absence from a fixture that never had them would prove nothing."""
    bulky = report() | {"interaction_samples_ms": [0.0] * 303, "interaction_cpu_samples_ms": [0.0]}

    summary = benchmark._summarize(
        benchmark.summarize_trials([bulky, bulky, bulky], manifest()), manifest(), Path("r.json")
    )

    assert "interaction_samples_ms" not in summary
    assert len(summary.splitlines()) == 5


def test_native_log_restores_stderr_even_when_the_body_raises(tmp_path) -> None:
    log = tmp_path / "native.log"
    probe = os.dup(2)
    os.close(probe)

    def write_then_fail() -> None:
        with benchmark._native_log_to(log):
            os.write(2, b"from the C side\n")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        write_then_fail()

    restored = os.dup(2)
    os.close(restored)
    assert restored == probe, "the descriptor table must come back to where it started"
    assert log.read_bytes() == b"from the C side\n"


def test_budget_oracle_accepts_wall_frame_boundary() -> None:
    measured = report()
    measured["interaction_p99_ms"] = 16.67
    assert evaluate(measured, manifest())


def test_budget_oracle_rejects_each_regression() -> None:
    controls = {
        "event_count": 100,
        "interaction_clock": "wall_time",
        "interaction_cpu_p99_ms": 16.68,
        "interaction_p99_ms": 16.68,
        "interaction_cpu_delta_p99_ms": 2.01,
        "interaction_wall_delta_p99_ms": 16.68,
        "ready_before_presentation_ratio": 0.989,
        "ready_before_presented": 99,
        "retained_rss_growth_mib": 256.01,
        "result_cache_entries": 4,
        "prefetch_cache_entries": 4,
        "presented": 100,
        "completed": 100,
        "geometry_apply_count": 99,
        "hit_test_count": 99,
        "focus_draw_count": 99,
        "tooltip_open_count": 99,
        "tooltip_scroll_count": 99,
        "failures": 1,
        "last_error": "render failed",
        "superseded": 1,
        "prefetch_dropped": 1,
        "cadence_misses": 1,
        "source_clear_current": True,
        "source_clear_hit_count": 1,
        "profile_switch_cache_entries": 1,
        "close_completed": False,
    }
    for field, value in controls.items():
        mutated = report()
        mutated[field] = value
        assert not evaluate(mutated, manifest()), field


@pytest.mark.parametrize("mutation", ["missing-workload", "lost-token", "extra-workload"])
def test_frame_workload_oracle_rejects_denominator_and_geometry_loss(mutation: str) -> None:
    measured = report()
    if mutation == "missing-workload":
        measured["simultaneous_frame_workloads"].pop()
    elif mutation == "lost-token":
        measured["simultaneous_frame_workloads"][-1]["found_tokens"] -= 1
    else:
        extra = dict(measured["simultaneous_frame_workloads"][-1])
        extra["active_events"] = 65
        measured["simultaneous_frame_workloads"].append(extra)

    assert not evaluate(measured, manifest())


@pytest.mark.parametrize("budget", sorted(benchmark.BUDGET_CLAUSES))
def test_trial_oracle_tolerates_one_outlier_in_any_latency_budget(budget: str) -> None:
    """One trial over a budget is a scheduler hiccup: a p99 taken over ~300 samples is the
    third-worst sample, so it moves with the runner. Parametrized over every clause because a
    median implementation that reads the comparator off one budget silently inverts the others."""
    if benchmark.BUDGET_CLAUSES[budget].across_trials != "median":
        pytest.skip(f"{budget} is conjunctive across trials by design")
    noisy = report()
    noisy[benchmark.BUDGET_CLAUSES[budget].metric] = _breaching(budget)

    summary = summarize_trials([report(), noisy, report()], manifest())

    assert summary["integration_budgets_passed"] is True


@pytest.mark.parametrize("budget", sorted(benchmark.BUDGET_CLAUSES))
def test_trial_oracle_rejects_a_budget_breached_in_the_majority(budget: str) -> None:
    """The direction that must still bite: a clause over budget in two of three trials moves the
    median, which is what a regression looks like."""
    noisy = report()
    noisy[benchmark.BUDGET_CLAUSES[budget].metric] = _breaching(budget)

    summary = summarize_trials([noisy, report(), noisy], manifest())

    assert summary["integration_budgets_passed"] is False


def _breaching(budget: str) -> float:
    """A value just the wrong side of `budget`, in whichever direction that clause compares."""
    limit = manifest()["budgets"][budget]
    return (
        limit * 0.5
        if benchmark.BUDGET_CLAUSES[budget].compare(0.0, limit) is False
        else limit + 0.01
    )


def test_trial_oracle_rejects_a_run_where_no_trial_was_ever_clean() -> None:
    """The relaxation's floor. Judging each clause on its median is weaker than the per-trial
    quorum it replaces — never stronger — so three trials each breaching a *different* clause have
    three passing medians and nothing clean anywhere. That is the shape of a broad regression, and
    without the clean-trial clause it aggregates to green."""
    first, second, third = report(), report(), report()
    first["interaction_p99_ms"] = 16.68
    second["interaction_cpu_delta_p99_ms"] = 2.01
    third["ready_before_presentation_ratio"] = 0.5

    summary = summarize_trials([first, second, third], manifest())

    assert summary["performance"]["clean_trials"] == 0
    assert summary["integration_budgets_passed"] is False


def test_a_cadence_miss_discards_the_trial_rather_than_failing_the_run() -> None:
    """A missed presentation cadence says the runner stalled, so that trial's latencies describe
    the stall. Counting it as a performance failure made the noisiest thing the harness can observe
    into the clause most likely to fail."""
    stalled = report()
    stalled["cadence_misses"] = 1
    stalled["interaction_p99_ms"] = 999.0

    summary = summarize_trials([report(), stalled, report()], manifest())

    assert summary["performance"]["valid_trials"] == 2
    assert summary["integration_budgets_passed"] is True


def test_a_run_without_enough_valid_trials_fails_rather_than_passing_on_one() -> None:
    """Discarding a stalled trial must not become a way to pass on a single measurement."""
    stalled = report()
    stalled["cadence_misses"] = 1

    summary = summarize_trials([stalled, report(), stalled], manifest())

    assert summary["performance"]["valid_trials"] == 1
    assert summary["integration_budgets_passed"] is False


def test_a_discarded_trial_leaves_an_even_count_that_must_not_be_interpolated() -> None:
    """`statistics.median` averages the two middle values at even counts, and discarding a
    cadence-stalled trial makes an even count routine — so a trial 92% over budget would be
    averaged with a fast one into a pass."""
    stalled = report()
    stalled["cadence_misses"] = 1
    slow, fast = report(), report()
    slow["interaction_cpu_p99_ms"] = 32.0
    fast["interaction_cpu_p99_ms"] = 0.5

    summary = summarize_trials([stalled, slow, fast], manifest())

    assert summary["performance"]["valid_trials"] == 2
    assert summary["integration_budgets_passed"] is False


@pytest.mark.parametrize("budget", sorted(benchmark.BUDGET_CLAUSES))
def test_one_trial_may_be_noisy_but_not_arbitrarily_far_past_a_budget(budget: str) -> None:
    """ "The median absorbs one bad trial" needs an upper bound, or a 4x breach reads the same as a
    1.1x one. The worst single-trial breach across the archived runs this rule accepts is 1.59x."""
    if benchmark.BUDGET_CLAUSES[budget].across_trials != "median":
        pytest.skip(f"{budget} is conjunctive across trials by design")
    clause = benchmark.BUDGET_CLAUSES[budget]
    limit = manifest()["budgets"][budget]
    wild = report()
    wild[clause.metric] = (
        limit * (benchmark.OUTLIER_TOLERANCE + 1)
        if clause.compare is operator.le
        else limit / (benchmark.OUTLIER_TOLERANCE + 1)
    )

    summary = summarize_trials([report(), wild, report()], manifest())

    assert summary["performance"]["clauses"][budget] is False
    assert summary["integration_budgets_passed"] is False


def test_a_leak_in_one_trial_still_fails_the_run() -> None:
    """`retained_rss_growth_mib` is a leak meter, not a noise-symmetric percentile — a median over
    it would let every other trial vote a leak away."""
    leaked = report()
    leaked["retained_rss_growth_mib"] = 512.0

    summary = summarize_trials([report(), leaked, report()], manifest())

    assert summary["integration_budgets_passed"] is False


def test_trial_oracle_rejects_any_functional_failure() -> None:
    failed = report()
    failed["failures"] = 1

    summary = summarize_trials([report(), failed, report()], manifest())

    assert summary["integration_budgets_passed"] is False
    assert summary["all_functional_invariants_passed"] is False


@pytest.mark.parametrize("schema", [None, 2])
def test_trial_oracle_rejects_missing_or_wrong_trial_schema(schema: int | None) -> None:
    incompatible = report()
    if schema is None:
        incompatible.pop("schema")
    else:
        incompatible["schema"] = schema

    summary = summarize_trials([report(), incompatible, report()], manifest())

    assert summary["integration_budgets_passed"] is False


def test_trial_summary_snapshots_caller_evidence() -> None:
    reports = [report(), report(), report()]
    summary = summarize_trials(reports, manifest())

    reports[0]["failures"] = 1

    assert summary["trials"][0]["report"]["failures"] == 0
    assert summary["integration_budgets_passed"] is True


def test_trial_execution_persists_error_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    class NativePanic(BaseException):
        pass

    attempts = iter(range(3))

    def run_trial(_manifest: dict, **_kwargs) -> dict:
        if next(attempts) == 1:
            raise NativePanic("renderer failed")
        return report()

    monkeypatch.setattr(benchmark, "run", run_trial)
    output = tmp_path / "trials.json"

    summary = benchmark.execute_trials(manifest(), None, output)
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert summary["integration_budgets_passed"] is False
    assert persisted["completed_trials"] == 2
    assert persisted["trials"][1]["status"] == "error"
    assert persisted["trials"][1]["error_type"] == "NativePanic"
    assert "renderer failed" in persisted["trials"][1]["traceback"]


def test_trial_execution_persists_interrupt_before_reraising(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    def interrupt(_manifest: dict, **_kwargs) -> dict:
        raise KeyboardInterrupt

    monkeypatch.setattr(benchmark, "run", interrupt)
    output = tmp_path / "trials.json"

    with pytest.raises(KeyboardInterrupt):
        benchmark.execute_trials(manifest(), None, output)
    persisted = json.loads(output.read_text(encoding="utf-8"))

    assert persisted["trials"][0]["status"] == "interrupted"
    assert persisted["trials"][0]["error_type"] == "KeyboardInterrupt"


def test_trial_run_closes_backend_after_mid_trial_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backends = []

    class RecordingBackend(benchmark.LibassGeometryBackend):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            backends.append(self)

    def fail_present(*_args, **_kwargs) -> bool:
        raise RuntimeError("interaction failed")

    monkeypatch.setattr(benchmark, "LibassGeometryBackend", RecordingBackend)
    monkeypatch.setattr(benchmark, "_present", fail_present)
    monkeypatch.setattr(
        benchmark,
        "_frame_workloads",
        lambda counts, _library_path: [
            {"active_events": count, "eligible_tokens": count, "found_tokens": count}
            for count in counts
        ],
    )

    with pytest.raises(RuntimeError, match="interaction failed"):
        benchmark.run(manifest())

    assert len(backends) == 1
    assert backends[0].closed is True


@pytest.mark.parametrize("trial_count", [1, 2, 4])
def test_trial_oracle_rejects_unlocked_denominator(trial_count: int) -> None:
    with pytest.raises(ValueError, match="locked denominator"):
        summarize_trials([report()] * trial_count, manifest())


def test_manifest_lock_rejects_budget_weakening(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text('{"schema": 1, "budgets": {"interaction_cpu_p99_ms": 8000}}', encoding="utf-8")

    with pytest.raises(ValueError, match="re-locking"):
        load_manifest(path)


def test_the_shipped_budgets_are_the_ones_under_review() -> None:
    """The SHA lock only proves the file is the one that was locked — recomputing the hash is part
    of any edit, so on its own it cannot tell a justified re-bless from a quiet weakening. Spelling
    the shipped numbers out here makes a re-lock produce a test diff someone has to approve.

    The hand-built `manifest()` above is a fixture for the oracle tests and deliberately differs
    (a tighter delta makes the boundary cases readable, and a zero interval keeps them instant), so
    it cannot serve this purpose.

    `interaction_wall_p99_ms` is 1000/60: the 60 Hz frame interval, not a fitted number. It is a
    floor tied to something a viewer perceives, so it does not move to accommodate a noisy runner —
    the estimator absorbs noise instead (`BUDGET_CLAUSES`).
    """
    shipped = load_manifest(
        Path(__file__).parents[1] / "tests/fixtures/native_subtitle_integration.json"
    )

    assert shipped["trials"] == 3
    assert shipped["budgets"] == {
        "interaction_cpu_p99_ms": 16.67,
        "interaction_wall_p99_ms": 16.67,
        "interaction_cpu_delta_p99_ms": 4.0,
        "interaction_wall_delta_p99_ms": 16.67,
        "ready_before_presentation_ratio": 0.99,
        "retained_rss_growth_mib": 256.0,
    }
    assert set(shipped["budgets"]) == set(benchmark.BUDGET_CLAUSES)


def test_the_harness_answers_every_option_the_geometry_gate_reads() -> None:
    """The fake mpv has to hold what production reads, or the benchmark measures a refusal.

    An option the gate reads and the fake does not answer comes back `None`, and `None` is not a
    supported value for several of them — so the track is refused, no geometry is ever submitted,
    and the trial reports the interaction as costing nothing. That is worse than a failure: the
    numbers look like a win.
    """
    from saitenka.app.native_subtitles import GATE_OPTIONS, _unsupported_render_inputs

    props = benchmark._IPC().props
    settings = {name: props.get(f"options/{name}") for name in GATE_OPTIONS}

    assert [name for name in GATE_OPTIONS if f"options/{name}" not in props] == []
    assert _unsupported_render_inputs(settings) == ()
