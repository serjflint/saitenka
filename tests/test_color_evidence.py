"""Metadata export keeps bounded numeric color outcomes without arbitrary labels."""

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
            "saitenka.subtitle.color_ack_ms": {"count": 2, "max": 25, "p95": float("nan")},
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
