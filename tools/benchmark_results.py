"""Validate and aggregate independent customSmallerIsBetter benchmark replicas."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BenchmarkResultError(ValueError):
    """A replica cannot be compared safely with the others."""


@dataclass(frozen=True, slots=True)
class Metric:
    name: str
    unit: str
    value: float


def _metric(item: Any, *, replica: int) -> Metric:
    if not isinstance(item, dict):
        raise BenchmarkResultError(f"replica {replica}: every metric must be an object")
    name, unit, value = item.get("name"), item.get("unit"), item.get("value")
    if not isinstance(name, str) or not name:
        raise BenchmarkResultError(f"replica {replica}: metric name must be a non-empty string")
    if not isinstance(unit, str) or not unit:
        raise BenchmarkResultError(f"replica {replica}: metric unit must be a non-empty string")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise BenchmarkResultError(f"replica {replica}, {name}: value must be a finite number")
    return Metric(name, unit, float(value))


def _parse_replica(document: Any, *, replica: int) -> list[Metric]:
    if not isinstance(document, list) or not document:
        raise BenchmarkResultError(f"replica {replica}: result must be a non-empty list")
    metrics = [_metric(item, replica=replica) for item in document]
    names = [metric.name for metric in metrics]
    if len(names) != len(set(names)):
        raise BenchmarkResultError(f"replica {replica}: metric names must be unique")
    return metrics


def _number(value: float) -> str:
    return f"{value:.6g}"


def _is_tail(name: str) -> bool:
    lowered = name.lower()
    return any(
        token in lowered for token in ("p95", "p99", "max", "worst", "jank", "dropped", "delayed")
    )


def load_manifest(path: Path) -> list[tuple[str, str]]:
    """Load the locked metric identity for one benchmark series."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return [(metric.name, metric.unit) for metric in _parse_replica(document, replica=0)]


def aggregate_replicas(documents: list[Any], *, min_replicas: int = 3) -> list[dict[str, Any]]:
    """Return one chart payload from independently produced replica documents."""
    if len(documents) < min_replicas:
        raise BenchmarkResultError(
            f"expected at least {min_replicas} replicas, got {len(documents)}"
        )

    replicas = [
        _parse_replica(document, replica=index) for index, document in enumerate(documents, 1)
    ]
    expected = [(metric.name, metric.unit) for metric in replicas[0]]
    for index, replica in enumerate(replicas[1:], 2):
        actual = [(metric.name, metric.unit) for metric in replica]
        if actual != expected:
            raise BenchmarkResultError(
                f"replica {index}: metric names and units differ from replica 1"
            )

    output: list[dict[str, Any]] = []
    for position, (name, unit) in enumerate(expected):
        values = [replica[position].value for replica in replicas]
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        spread = (
            f"{len(values)} replicas; min {_number(min(values))}; max {_number(max(values))}; "
            f"MAD {_number(mad)}"
        )
        if _is_tail(name):
            spread += f"; worst {_number(max(values))}"
        output.append({"name": name, "unit": unit, "value": round(median, 6), "range": spread})
    return output


def validate_manifest(aggregate: list[dict[str, Any]], manifest: list[tuple[str, str]]) -> None:
    actual = [(item["name"], item["unit"]) for item in aggregate]
    if actual != manifest:
        raise BenchmarkResultError(
            "aggregate metric names and units differ from the locked manifest"
        )


def enforce_maximums(documents: list[Any], limits: dict[str, float]) -> None:
    """Fail when any independent replica exceeds a named hard ceiling."""
    if not limits:
        return
    for replica_index, document in enumerate(documents, 1):
        metrics = {
            metric.name: metric.value for metric in _parse_replica(document, replica=replica_index)
        }
        for name, limit in limits.items():
            if name not in metrics:
                raise BenchmarkResultError(
                    f"replica {replica_index}: missing limited metric {name!r}"
                )
            if metrics[name] > limit:
                raise BenchmarkResultError(
                    f"replica {replica_index}: {name} {metrics[name]:g} exceeds maximum {limit:g}"
                )


def render_markdown(
    aggregate: list[dict[str, Any]],
    *,
    dashboard_url: str = "https://serjflint.github.io/saitenka/dev/bench/",
) -> str:
    """Render the same aggregate as a compact PR summary."""
    lines = [
        "<!-- saitenka-continuous-benchmarks -->",
        "### Continuous benchmarks",
        "",
        "| Metric | Median | Replica spread |",
        "| --- | ---: | --- |",
    ]
    lines.extend(
        f"| {item['name']} | {_number(float(item['value']))} {item['unit']} | {item['range']} |"
        for item in aggregate
    )
    lines.extend(
        [
            "",
            "Advisory signal from independent GitHub-hosted runners; `poe perf-check` is the hard guard.",
            f"[Full history]({dashboard_url})",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replicas", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--max-value",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="fail if any replica exceeds this named metric ceiling",
    )
    parser.add_argument("--min-replicas", type=int, default=3)
    args = parser.parse_args(argv)

    if len({path.resolve() for path in args.replicas}) != len(args.replicas):
        raise BenchmarkResultError("replica paths must be distinct")
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in args.replicas]
    aggregate = aggregate_replicas(documents, min_replicas=args.min_replicas)
    if args.manifest:
        validate_manifest(aggregate, load_manifest(args.manifest))
    args.output.write_text(json.dumps(aggregate, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(render_markdown(aggregate), encoding="utf-8")
    limits: dict[str, float] = {}
    for item in args.max_value:
        name, separator, raw_limit = item.rpartition("=")
        if not separator or not name:
            raise BenchmarkResultError("--max-value must be NAME=VALUE")
        try:
            limits[name] = float(raw_limit)
        except ValueError as error:
            raise BenchmarkResultError("--max-value VALUE must be numeric") from error
    enforce_maximums(documents, limits)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
