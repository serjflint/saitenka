from __future__ import annotations

import os
from pathlib import Path

import pytest
from oracle.parity import _assert_pinned_revision, compare_with_yomitan

_ORACLE_DIRECTORY = Path(__file__).parents[1] / "oracle"


def test_differential_oracle_rejects_an_unreviewed_upstream_revision():
    with pytest.raises(RuntimeError, match="expected pinned revision"):
        _assert_pinned_revision("moved-checkout", _ORACLE_DIRECTORY / "upstream-lock.json")


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_pinned_yomitan_surface_matches_headless_oracle():
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the headless differential")

    report = compare_with_yomitan(
        Path(checkout),
        runner=_ORACLE_DIRECTORY / "yomitan_oracle.mjs",
        upstream_lock=_ORACLE_DIRECTORY / "upstream-lock.json",
    )

    assert report.passed, report.as_markdown()
