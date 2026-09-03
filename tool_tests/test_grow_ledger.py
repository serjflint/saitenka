"""Tests run by `poe loop-tools-test`, or explicitly:
    uv run python -m pytest tool_tests/test_grow_ledger.py

Locks the two properties that make the loop terminate (idempotent under unrelated churn, reopens on a
real target change) — the Q3 result from `vibe/proto_grow_ledger.py`, made permanent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import grow_ledger as gl

MANIFEST = {"type": "manifest", "toolset_version": 1}
REFLECTION = {"introspection": "audited", "findings": [], "appended": True, "escalations": []}

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


def test_target_sha_reopens_on_a_decorator_swap():  # C7 — get_source_segment used to drop decorators
    before = "class D:\n    @property\n    def v(self):\n        return 1\n"
    after = "class D:\n    @cached_property\n    def v(self):\n        return 1\n"
    assert gl.target_sha(before, "D.v") != gl.target_sha(after, "D.v")


def test_target_sha_reopens_on_a_redefinition_body_change():  # C7 — first-node-only used to miss this
    before = "def f():\n    return 1\n\n\ndef f():\n    return 2\n"
    after = "def f():\n    return 1\n\n\ndef f():\n    return 3\n"  # only the SECOND f changes
    assert gl.target_sha(before, "f") != gl.target_sha(after, "f")


def test_target_sha_ignores_a_comment_only_change():  # normalised via ast.unparse (strengthens P1)
    before = "def crisp(text, scale):\n    return (text.upper(), scale)\n"
    after = "def crisp(text, scale):\n    # a new comment\n    return (text.upper(), scale)\n"
    assert gl.target_sha(before, "crisp") == gl.target_sha(after, "crisp")


# --- ledger status ------------------------------------------------------------------------------


def _repo(tmp_path: Path, module_src: str = MODULE_V1) -> Path:
    (tmp_path / f"{gl.SRC}/app").mkdir(parents=True)
    (tmp_path / f"{gl.SRC}/app/x.py").write_text(module_src, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text(
        "def test_crisp():\n    assert True\n", encoding="utf-8"
    )
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
        "contract_version": gl.CONTRACT_VERSION,
        "reflection": REFLECTION,
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


def test_no_gap_audit_is_current_until_the_module_or_mapped_tests_change(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    prepared = gl.prepare_audit_record(
        {
            "audit_module": "app/x.py",
            "tests": ["tests/test_x.py"],
            "state": "no-gap",
            "reflection": REFLECTION,
        },
        root,
        ledger,
    )
    ledger.append(prepared)

    assert ledger.audit_status("app/x.py", root) == gl.AUDITED_CURRENT

    (root / "tests/test_x.py").write_text("def test_crisp():\n    assert False\n", encoding="utf-8")
    assert ledger.audit_status("app/x.py", root) == gl.STALE_AUDIT

    (root / "tests/test_x.py").write_text("def test_crisp():\n    assert True\n", encoding="utf-8")
    (root / f"{gl.SRC}/app/x.py").write_text(MODULE_V3, encoding="utf-8")
    assert ledger.audit_status("app/x.py", root) == gl.STALE_AUDIT


def test_no_gap_audit_reopens_when_the_toolset_changes(tmp_path):
    root = _repo(tmp_path)
    prepared = {
        "audit_module": "app/x.py",
        "tests": ["tests/test_x.py"],
        "audit_sha": gl.audit_sha(root, "app/x.py"),
        "toolset_version": 1,
        "state": "no-gap",
    }
    ledger = _ledger(root, [{"type": "manifest", "toolset_version": 2}, prepared])

    assert ledger.audit_status("app/x.py", root) == gl.STALE_TOOLSET


def test_no_gap_audit_reopens_when_the_lifecycle_contract_changes(tmp_path):
    root = _repo(tmp_path)
    prepared = {
        "audit_module": "app/x.py",
        "tests": ["tests/test_x.py"],
        "audit_sha": gl.audit_sha(root, "app/x.py"),
        "toolset_version": 1,
        "contract_version": gl.CONTRACT_VERSION - 1,
        "state": "no-gap",
    }
    ledger = _ledger(root, [MANIFEST, prepared])

    assert ledger.audit_status("app/x.py", root) == gl.STALE_CONTRACT


def test_closed_gap_reopens_when_the_lifecycle_contract_changes(tmp_path):
    root = _repo(tmp_path)
    record = _rec(state="closed", contract_version=gl.CONTRACT_VERSION - 1)
    ledger = _ledger(root, [MANIFEST, record])

    assert ledger.status(record["gap_id"], root) == gl.STALE_CONTRACT


def test_current_contract_without_reflection_cannot_suppress_a_gap(tmp_path):
    root = _repo(tmp_path)
    record = _rec(state="closed")
    record.pop("reflection")
    ledger = _ledger(root, [MANIFEST, record])

    assert ledger.status(record["gap_id"], root) == gl.STALE_CONTRACT


def test_prepare_audit_record_owns_hash_and_manifest_version(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [{"type": "manifest", "toolset_version": 7}])

    prepared = gl.prepare_audit_record(
        {
            "audit_module": "app/x.py",
            "tests": ["tests/test_x.py"],
            "state": "no-gap",
            "reflection": REFLECTION,
        },
        root,
        ledger,
    )

    assert prepared["audit_sha"] == gl.audit_sha(root, "app/x.py")
    assert prepared["toolset_version"] == 7
    assert prepared["contract_version"] == gl.CONTRACT_VERSION
    assert prepared["reflection"] == REFLECTION


def test_prepare_record_rejects_missing_or_unwritten_reflection(tmp_path):
    import pytest

    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    record = {"source": "invariant", "target_symbol": "app/x.py::crisp", "dimension": "x"}
    with pytest.raises(TypeError, match="reflection receipt"):
        gl.prepare_record(record, root, ledger, require_reflection=True)
    record["reflection"] = {**REFLECTION, "appended": False}
    with pytest.raises(ValueError, match="durably appended"):
        gl.prepare_record(record, root, ledger, require_reflection=True)


def test_prepare_audit_record_requires_evidence_and_no_gap_state(tmp_path):
    import pytest

    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    with pytest.raises(ValueError, match="non-empty tests"):
        gl.prepare_audit_record(
            {"audit_module": "app/x.py", "tests": [], "state": "no-gap"}, root, ledger
        )
    with pytest.raises(ValueError, match="state must be no-gap"):
        gl.prepare_audit_record(
            {"audit_module": "app/x.py", "tests": ["tests/test_x.py"], "state": "closed"},
            root,
            ledger,
        )


def test_prepare_audit_record_rejects_missing_or_directory_test_paths(tmp_path):
    import pytest

    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    for test_path in ["tests/does-not-exist.py", "tests"]:
        with pytest.raises(ValueError, match=r"existing test_\*\.py"):
            gl.prepare_audit_record(
                {
                    "audit_module": "app/x.py",
                    "tests": [test_path],
                    "state": "no-gap",
                    "reflection": REFLECTION,
                },
                root,
                ledger,
            )


def test_prepare_record_owns_identity_and_manifest_version(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [{"type": "manifest", "toolset_version": 7}])

    prepared = gl.prepare_record(
        {
            "source": "invariant",
            "target_symbol": "app/x.py::crisp",
            "dimension": "scale=2.0",
            "state": "dry-run",
        },
        root,
        ledger,
    )

    assert prepared["gap_id"] == gl.gap_id("invariant", "app/x.py::crisp", "scale=2.0")
    assert prepared["target_sha"] == gl.target_sha(MODULE_V1, "crisp")
    assert prepared["toolset_version"] == 7


def test_append_cli_fills_identity_fields(monkeypatch, tmp_path, capsys):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    record = {
        "source": "invariant",
        "target_symbol": "app/x.py::crisp",
        "dimension": "scale=2.0",
        "state": "dry-run",
        "reflection": REFLECTION,
    }
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grow_ledger.py",
            "--repo",
            str(root),
            "--ledger",
            str(ledger.path),
            "append",
            "--record-json",
            json.dumps(record),
        ],
    )

    assert gl._main() == 0

    written = json.loads(capsys.readouterr().out)
    assert gl.Ledger.load(ledger.path).latest(written["gap_id"])["state"] == "dry-run"


def test_append_cli_fills_no_gap_audit_identity(monkeypatch, tmp_path, capsys):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    record = {
        "audit_module": "app/x.py",
        "tests": ["tests/test_x.py"],
        "state": "no-gap",
        "reflection": REFLECTION,
    }
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grow_ledger.py",
            "--repo",
            str(root),
            "--ledger",
            str(ledger.path),
            "append",
            "--record-json",
            json.dumps(record),
        ],
    )

    assert gl._main() == 0

    written = json.loads(capsys.readouterr().out)
    assert gl.Ledger.load(ledger.path).latest_audit("app/x.py")["audit_sha"] == written["audit_sha"]


def test_prepare_record_rejects_a_target_outside_source_root(tmp_path):
    import pytest

    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    with pytest.raises(ValueError, match="stay under"):
        gl.prepare_record(
            {
                "source": "invariant",
                "target_symbol": "../../../tools/x.py::_main",
                "dimension": "escape",
            },
            root,
            ledger,
        )


def test_cli_reports_invalid_json_without_a_traceback(monkeypatch, tmp_path, capsys):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "grow_ledger.py",
            "--repo",
            str(root),
            "--ledger",
            str(ledger.path),
            "append",
            "--record-json",
            "[",
        ],
    )

    assert gl.main() == 2
    assert capsys.readouterr().err.startswith("grow-ledger: error:")
