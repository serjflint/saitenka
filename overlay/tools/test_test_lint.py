from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_lint

HITS = [
    {"file": "tests/test_a.py", "ruleId": "actionable"},
    {"file": "tests/test_a.py", "ruleId": "advisory"},
    {"file": "tests/test_b.py", "ruleId": "actionable"},
]


def test_select_hits_filters_by_file_and_rule():
    assert test_lint.select_hits(HITS, files={"tests/test_a.py"}, rules={"actionable"}) == [HITS[0]]


def test_select_hits_without_filters_preserves_all_findings():
    assert test_lint.select_hits(HITS) == HITS
