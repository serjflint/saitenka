"""Metadata export keeps bounded numeric color outcomes without arbitrary labels."""

from itertools import combinations, product

from saitenka.app.color_evidence import safe_color_metrics


def test_color_metadata_keeps_outcomes_and_discards_untrusted_labels():
    result = safe_color_metrics(
        {
            "saitenka.subtitle.color_outcomes": {
                "value": 3,
                "by": {
                    "kind=navigation,reason=replaced,status=partial": 2,
                    "kind=/private/subtitle.ass": 1,
                },
            },
            "saitenka.subtitle.color_ack_ms": {
                "count": 2,
                "max": 25,
                "p50": -1,
                "p95": float("nan"),
                "p99": float("inf"),
                "sum": 1e16,
                "private": 7,
            },
            "other": {"value": 42},
        }
    )

    assert result == {
        "status": "collected",
        "metrics": {
            "saitenka.subtitle.color_outcomes": {
                "value": 3,
                "by": {
                    "kind=navigation,reason=replaced,status=partial": {"value": 2},
                },
            },
            "saitenka.subtitle.color_ack_ms": {"count": 2, "max": 25},
        },
    }


def test_color_metadata_bounds_label_series():
    values = {
        "kind": ("natural", "navigation"),
        "endpoint": ("first", "complete"),
        "status": ("complete", "partial", "failed", "unknown"),
        "reason": ("replaced", "shutdown", "blank", "source-changed", "episode-changed"),
    }
    labels = {}
    for size in (1, 2, 3):
        for keys in combinations(values, size):
            for selected in product(*(values[key] for key in keys)):
                labels[
                    ",".join(f"{key}={value}" for key, value in zip(keys, selected, strict=True))
                ] = 1
    assert len(labels) > 128

    result = safe_color_metrics(
        {"saitenka.subtitle.color_outcomes": {"value": 1, "by": dict(list(labels.items())[:129])}}
    )

    assert len(result["metrics"]["saitenka.subtitle.color_outcomes"]["by"]) == 128
