"""The external oracle for FSRS retrievability: py-fsrs's own computation.

`app.fsrs.retrievability` hand-copies FSRS-6's decay formula. `test_fsrs.py` checks it against a
re-transcription of that same formula (self-consistent); this checks it against the **real** py-fsrs
over a vendored grid of `(stability, elapsed, decay)` reference vectors
(`tools/gen_fsrs_vectors.py`), so a transcription drift or a changed upstream constant is caught.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from overlay.app import fsrs

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "fsrs" / "py_fsrs_retrievability.json").read_text(
        encoding="utf-8"
    )
)["cases"]


@pytest.mark.parametrize(
    "case",
    _CASES,
    ids=[f"s{c['s']}_e{c['elapsed']:.0f}_d{c['decay']}" for c in _CASES],
)
def test_retrievability_matches_py_fsrs(case):
    got = fsrs.retrievability(case["s"], case["elapsed"], case["decay"])
    assert got == pytest.approx(case["r"], abs=1e-9)
