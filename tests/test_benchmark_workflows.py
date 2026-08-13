from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _workflow(name: str):
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_pr_benchmark_has_three_replicas_one_aggregate_and_one_summary_comment():
    jobs = _workflow("perf.yml")["jobs"]

    assert jobs["measure"]["strategy"]["matrix"]["replica"] == [1, 2, 3]
    assert jobs["compare"]["needs"] == ["resolve", "measure"]
    assert jobs["store"]["needs"] == ["resolve", "measure"]
    compare_steps = jobs["compare"]["steps"]
    assert any("benchmark-summary.md" in step.get("run", "") for step in compare_steps)
    assert sum("actions/github-script" in step.get("uses", "") for step in compare_steps) == 1
    compare_action = next(
        step for step in compare_steps if "github-action-benchmark" in step.get("uses", "")
    )
    store_action = next(
        step for step in jobs["store"]["steps"] if "github-action-benchmark" in step.get("uses", "")
    )
    assert compare_action["with"]["auto-push"] is False
    assert compare_action["with"]["save-data-file"] is False
    assert jobs["store"]["if"] == "github.event_name != 'pull_request'"
    assert store_action["with"]["auto-push"] is True
    assert jobs["store"]["concurrency"]["group"] == "benchmark-pages"
    assert jobs["store"]["concurrency"]["queue"] == "max"


def test_manual_backfill_checks_out_and_reports_the_selected_revision():
    workflow = _workflow("perf.yml")
    jobs = workflow["jobs"]
    measure_steps = jobs["measure"]["steps"]
    checkouts = [step for step in measure_steps if "actions/checkout" in step.get("uses", "")]
    store_action = next(
        step for step in jobs["store"]["steps"] if "github-action-benchmark" in step.get("uses", "")
    )

    assert "^[0-9a-fA-F]{40}$" in jobs["resolve"]["steps"][0]["run"]
    assert "'backfill'" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["queue"] == "max"
    assert checkouts[0]["with"]["ref"] == "${{ needs.resolve.outputs.revision }}"
    assert checkouts[1]["with"] == {
        "ref": "${{ github.workflow_sha }}",
        "path": ".benchmark-harness",
    }
    benchmark_command = next(
        step["run"] for step in measure_steps if "bench_core.py" in step.get("run", "")
    )
    assert "$BENCHMARK_HARNESS/examples/bench_core.py" in benchmark_command
    assert store_action["with"]["ref"] == "${{ needs.resolve.outputs.revision }}"
    aggregate_steps = [*jobs["compare"]["steps"], *jobs["store"]["steps"]]
    assert (
        sum(
            "--manifest benchmarks/continuous-core-metrics.json" in step.get("run", "")
            for step in aggregate_steps
        )
        == 2
    )


def test_weekly_benchmarks_aggregate_three_live_and_lifecycle_replicas():
    jobs = _workflow("e2e.yml")["jobs"]

    for surface in ("jank", "lifecycle"):
        measure = jobs[f"{surface}-measure"]
        store = jobs[f"{surface}-store"]
        assert measure["strategy"]["matrix"]["replica"] == [1, 2, 3]
        assert measure["if"] == "github.event_name != 'pull_request'"
        assert store["if"] == "github.event_name != 'pull_request'"
        assert store["needs"] == ["resolve", f"{surface}-measure"]
        assert store["concurrency"]["group"] == "benchmark-pages"
        assert store["concurrency"]["queue"] == "max"
        store_action = next(
            step for step in store["steps"] if "github-action-benchmark" in step.get("uses", "")
        )
        assert store_action["with"]["auto-push"] is True
    lifecycle_command = next(
        step["run"]
        for step in jobs["lifecycle-measure"]["steps"]
        if "bench_responsiveness.py" in step.get("run", "")
    )
    assert "--reps 3" in lifecycle_command
    jank_command = next(
        step["run"]
        for step in jobs["jank-measure"]["steps"]
        if "jank_live.py" in step.get("run", "")
    )
    assert "--max-drops" not in jank_command
    jank_store_steps = jobs["jank-store"]["steps"]
    publish_index = next(
        index
        for index, step in enumerate(jank_store_steps)
        if "github-action-benchmark" in step.get("uses", "")
    )
    guard_index = next(
        index
        for index, step in enumerate(jank_store_steps)
        if "--max-value 'live jank: total dropped frames=30'" in step.get("run", "")
    )
    assert publish_index < guard_index
    assert "permissions" not in jobs["e2e"]


def test_manual_e2e_backfill_uses_current_harness_and_labels_selected_revision():
    workflow = _workflow("e2e.yml")
    jobs = workflow["jobs"]

    assert "^[0-9a-fA-F]{40}$" in jobs["resolve"]["steps"][0]["run"]
    assert "inputs.revision || github.ref" in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert "queue" not in workflow["concurrency"]
    for surface, script in (("jank", "jank_live.py"), ("lifecycle", "bench_responsiveness.py")):
        measure = jobs[f"{surface}-measure"]
        checkouts = [
            step for step in measure["steps"] if "actions/checkout" in step.get("uses", "")
        ]
        assert measure["needs"] == "resolve"
        assert checkouts[0]["with"]["ref"] == "${{ needs.resolve.outputs.revision }}"
        assert checkouts[1]["with"] == {
            "ref": "${{ github.workflow_sha }}",
            "path": ".benchmark-harness",
        }
        command = next(step["run"] for step in measure["steps"] if script in step.get("run", ""))
        assert f"$BENCHMARK_HARNESS/examples/{script}" in command

        store = jobs[f"{surface}-store"]
        assert store["needs"] == ["resolve", f"{surface}-measure"]
        action = next(
            step for step in store["steps"] if "github-action-benchmark" in step.get("uses", "")
        )
        assert action["with"]["ref"] == "${{ needs.resolve.outputs.revision }}"

    assert "inputs.revision == ''" in jobs["e2e"]["if"]
    assert "inputs.revision == ''" in jobs["install-smoke"]["if"]
