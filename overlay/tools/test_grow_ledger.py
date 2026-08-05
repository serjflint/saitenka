"""Tests for the grow ledger lib. Run explicitly (tools/ is outside `poe all`):
    uv run python -m pytest tools/test_grow_ledger.py

Locks the two properties that make the loop terminate (idempotent under unrelated churn, reopens on a
real target change) — the Q3 result from `vibe/proto_grow_ledger.py`, made permanent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grow_ledger as gl

MANIFEST = {"type": "manifest", "toolset_version": 1}

MODULE_V1 = "def helper(x):\n    return x + 1\n\n\ndef crisp(text, scale):\n    return (text.upper(), scale)\n"
# unrelated edit ABOVE the target; crisp() is byte-identical
MODULE_V2 = "def helper(x):\n    # a new comment\n    y = x + 1\n    return y\n\n\ndef crisp(text, scale):\n    return (text.upper(), scale)\n"
# the target symbol itself changes
MODULE_V3 = "def helper(x):\n    return x + 1\n\n\ndef crisp(text, scale):\n    return (text.lower(), scale)\n"


# --- gap identity -------------------------------------------------------------------------------


def test_gap_id_is_deterministic():
    a = gl.gap_id("invariant", "app/x.py::crisp", "agreement@scale=2.0")
    b = gl.gap_id("invariant", "app/x.py::crisp", "agreement@scale=2.0")
    assert a == b


def test_gap_id_differs_by_dimension():
    a = gl.gap_id("invariant", "app/x.py::crisp", "scale=2.0")
    b = gl.gap_id("invariant", "app/x.py::crisp", "scale=3.0")
    assert a != b


# --- target_sha: the P1 / P2 termination properties ---------------------------------------------


def test_target_sha_is_stable_under_unrelated_line_drift():  # P1
    assert gl.target_sha(MODULE_V1, "crisp") == gl.target_sha(MODULE_V2, "crisp")


def test_target_sha_changes_when_the_target_symbol_changes():  # P2
    assert gl.target_sha(MODULE_V1, "crisp") != gl.target_sha(MODULE_V3, "crisp")


def test_target_sha_resolves_a_dotted_method():
    src = "class Dict:\n    def _entry(self, row):\n        return row\n"
    other = "class Dict:\n    def _entry(self, row):\n        return row[0]\n"
    assert gl.target_sha(src, "Dict._entry") != gl.target_sha(other, "Dict._entry")


def test_symbol_source_raises_for_a_missing_symbol():
    import pytest

    with pytest.raises(KeyError):
        gl.symbol_source(MODULE_V1, "nonexistent")


# --- ledger status ------------------------------------------------------------------------------


def _repo(tmp_path: Path, module_src: str = MODULE_V1) -> Path:
    (tmp_path / f"{gl.SRC}/app").mkdir(parents=True)
    (tmp_path / f"{gl.SRC}/app/x.py").write_text(module_src, encoding="utf-8")
    return tmp_path


def _ledger(root: Path, records: list[dict]) -> gl.Ledger:
    p = root / ".ledger.grow.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return gl.Ledger.load(p)


def _rec(*, state: str, module_src: str = MODULE_V1, **extra) -> dict:
    gid = gl.gap_id("invariant", "app/x.py::crisp", "scale=2.0")
    return {
        "gap_id": gid,
        "target_symbol": "app/x.py::crisp",
        "dimension": "scale=2.0",
        "target_sha": gl.target_sha(module_src, "crisp"),
        "toolset_version": 1,
        "state": state,
        **extra,
    }


def test_status_is_unseen_without_a_record(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    assert ledger.status(gl.gap_id("invariant", "app/x.py::crisp", "scale=2.0"), root) == gl.UNSEEN


def test_status_is_closed_current_when_closed_and_target_unchanged(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="closed")
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.status(rec["gap_id"], root) == gl.CLOSED_CURRENT


def test_status_reopens_when_the_target_symbol_changes(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="closed")  # recorded against V1's crisp
    ledger = _ledger(root, [MANIFEST, rec])
    (root / f"{gl.SRC}/app/x.py").write_text(MODULE_V3, encoding="utf-8")  # crisp changed
    assert ledger.status(rec["gap_id"], root) == gl.STALE_TARGET


def test_status_stays_closed_under_unrelated_drift(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="closed")
    ledger = _ledger(root, [MANIFEST, rec])
    (root / f"{gl.SRC}/app/x.py").write_text(MODULE_V2, encoding="utf-8")  # edit ABOVE crisp only
    assert ledger.status(rec["gap_id"], root) == gl.CLOSED_CURRENT


def test_status_is_stale_when_the_symbol_moved_instead_of_raising(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="closed")
    ledger = _ledger(root, [MANIFEST, rec])
    (root / f"{gl.SRC}/app/x.py").write_text("def other():\n    return 0\n", encoding="utf-8")
    assert ledger.status(rec["gap_id"], root) == gl.STALE_TARGET  # reopen, never crash


def test_status_is_unclosable_when_recorded_infeasible(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="unclosable")
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.status(rec["gap_id"], root) == gl.UNCLOSABLE


def test_status_goes_stale_when_the_toolset_version_bumps(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="closed")
    ledger = _ledger(root, [{"type": "manifest", "toolset_version": 2}, rec])
    assert ledger.status(rec["gap_id"], root) == gl.STALE_TOOLSET


def test_latest_returns_the_most_recent_record(tmp_path):
    root = _repo(tmp_path)
    gid = gl.gap_id("invariant", "app/x.py::crisp", "scale=2.0")
    old = {"gap_id": gid, "target_symbol": "app/x.py::crisp", "state": "open"}
    new = {"gap_id": gid, "target_symbol": "app/x.py::crisp", "state": "closed"}
    ledger = _ledger(root, [MANIFEST, old, new])
    assert ledger.latest(gid)["state"] == "closed"


def test_filed_maps_gap_to_issue_refs(tmp_path):
    root = _repo(tmp_path)
    rec = _rec(state="open", filed=["#201"])
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.filed() == {rec["gap_id"]: ["#201"]}


def test_append_round_trips_a_record(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    rec = _rec(state="closed")
    ledger.append(rec)
    assert gl.Ledger.load(root / ".ledger.grow.jsonl").latest(rec["gap_id"])["state"] == "closed"
