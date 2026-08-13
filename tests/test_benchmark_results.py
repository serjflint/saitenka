import importlib.util
import json
import sys
from pathlib import Path

import pytest

RESULTS_PATH = Path(__file__).resolve().parent.parent / "tools" / "benchmark_results.py"
SPEC = importlib.util.spec_from_file_location("benchmark_results", RESULTS_PATH)
RESULTS = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RESULTS
SPEC.loader.exec_module(RESULTS)
BenchmarkResultError = RESULTS.BenchmarkResultError
aggregate_replicas = RESULTS.aggregate_replicas
enforce_maximums = RESULTS.enforce_maximums
main = RESULTS.main
render_markdown = RESULTS.render_markdown
validate_manifest = RESULTS.validate_manifest


def _replica(render: float, tail: float = 4.0):
    return [
        {"name": "render median", "unit": "ms", "value": render},
        {"name": "render p99", "unit": "ms", "value": tail},
    ]


def test_aggregate_replicas_publishes_median_and_replica_spread():
    result = aggregate_replicas([_replica(1.0, 8.0), _replica(5.0, 6.0), _replica(3.0, 10.0)])

    assert result == [
        {
            "name": "render median",
            "unit": "ms",
            "value": 3.0,
            "range": "3 replicas; min 1; max 5; MAD 2",
        },
        {
            "name": "render p99",
            "unit": "ms",
            "value": 8.0,
            "range": "3 replicas; min 6; max 10; MAD 2; worst 10",
        },
    ]


def test_aggregate_replicas_keeps_worst_replica_for_named_worst_metric():
    replicas = [
        [{"name": "lifecycle: worst frame", "unit": "ms", "value": value}]
        for value in (4.0, 9.0, 6.0)
    ]

    assert aggregate_replicas(replicas)[0]["range"].endswith("; worst 9")


def test_aggregate_replicas_rejects_fewer_than_three_independent_runs():
    with pytest.raises(BenchmarkResultError, match="at least 3 replicas"):
        aggregate_replicas([_replica(1.0), _replica(2.0)])


@pytest.mark.parametrize(
    ("replicas", "message"),
    [
        (
            [_replica(1.0), _replica(2.0), [{"name": "other", "unit": "ms", "value": 3}]],
            "metric names",
        ),
        (
            [_replica(1.0), _replica(2.0), [{"name": "render median", "unit": "s", "value": 3}]],
            "metric names",
        ),
        (
            [_replica(1.0), _replica(2.0), _replica(float("inf"))],
            "finite number",
        ),
    ],
)
def test_aggregate_replicas_rejects_schema_or_value_drift(replicas, message):
    with pytest.raises(BenchmarkResultError, match=message):
        aggregate_replicas(replicas)


def test_cli_reads_replica_files_and_writes_one_aggregate(tmp_path):
    paths = []
    for index, value in enumerate((1.0, 3.0, 2.0), 1):
        path = tmp_path / f"replica-{index}.json"
        path.write_text(json.dumps(_replica(value)), encoding="utf-8")
        paths.append(path)
    output = tmp_path / "aggregate.json"

    markdown = tmp_path / "summary.md"
    assert (
        main(
            [
                *(str(path) for path in paths),
                "--output",
                str(output),
                "--markdown",
                str(markdown),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8"))[0]["value"] == 2.0
    assert markdown.read_text(encoding="utf-8") == render_markdown(
        json.loads(output.read_text(encoding="utf-8"))
    )


def test_cli_rejects_reusing_one_file_as_multiple_replicas(tmp_path):
    path = tmp_path / "replica.json"
    path.write_text(json.dumps(_replica(1.0)), encoding="utf-8")

    with pytest.raises(BenchmarkResultError, match="paths must be distinct"):
        main([str(path), str(path), str(path), "--output", str(tmp_path / "aggregate.json")])


def test_manifest_rejects_a_backfill_with_a_different_metric_surface():
    aggregate = aggregate_replicas([_replica(1.0), _replica(2.0), _replica(3.0)])

    with pytest.raises(BenchmarkResultError, match="locked manifest"):
        validate_manifest(aggregate, [("different metric", "ms")])


def test_hard_ceiling_checks_every_replica_not_only_the_median():
    replicas = [_replica(1.0), _replica(2.0), _replica(50.0)]

    with pytest.raises(BenchmarkResultError, match="replica 3"):
        enforce_maximums(replicas, {"render median": 10.0})


def test_markdown_is_a_short_table_from_the_published_values():
    aggregate = aggregate_replicas([_replica(1.0), _replica(2.0), _replica(3.0)])

    summary = render_markdown(aggregate, dashboard_url="https://example.test/bench")

    assert "| render median | 2 ms | 3 replicas; min 1; max 3; MAD 1 |" in summary
    assert "[Full history](https://example.test/bench)" in summary
