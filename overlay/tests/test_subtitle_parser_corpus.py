"""The external oracle for the subtitle parser: SubMiner's own test corpus.

`sub_index.py` is a faithful port of SubMiner's `subtitle-cue-parser.ts`, which was covered here only
by the app's own indirect tests. These vectors are transcribed from SubMiner's
`subtitle-cue-parser.test.ts` (see the fixture header for provenance) — same-algorithm conformance,
so a divergence means the port drifted from upstream. Assertions are on the observable `SubCue`
(start, end, text), matching SubMiner's `startTime`/`endTime`/`text`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from overlay.app.sub_index import parse_ass, parse_cues, parse_srt

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "subtitle" / "subminer_parser_cases.json").read_text(
        encoding="utf-8"
    )
)["cases"]


def _run(case: dict) -> list[list]:
    if case["fn"] == "srt":
        cues = parse_srt(case["content"])
    elif case["fn"] == "ass":
        cues = parse_ass(case["content"])
    else:
        cues = parse_cues(case["content"], case["filename"])
    return [[round(c.start, 3), round(c.end, 3), c.text] for c in cues]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_matches_subminer_parser_corpus(case):
    assert _run(case) == [list(e) for e in case["expect"]]
