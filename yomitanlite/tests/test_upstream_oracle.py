from __future__ import annotations

import os
from pathlib import Path

import pytest
from yomitanlite.parity import _assert_pinned_revision, compare_with_yomitan


def test_differential_oracle_rejects_an_unreviewed_upstream_revision():
    with pytest.raises(RuntimeError, match="expected pinned revision"):
        _assert_pinned_revision("moved-checkout")


@pytest.mark.integration
@pytest.mark.timeout(30)
def test_pinned_yomitan_surface_matches_headless_oracle():
    checkout = os.environ.get("YOMITAN_CHECKOUT")
    if checkout is None:
        pytest.skip("set YOMITAN_CHECKOUT to run the headless differential")

    report = compare_with_yomitan(Path(checkout))

    assert report.passed, report.as_markdown()
