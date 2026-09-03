"""Tests run by `poe loop-tools-test`, or explicitly:
uv run python -m pytest tool_tests/test_sharpen_ledger.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
import sharpen_ledger as sl

MANIFEST = {"type": "manifest", "toolset_version": 1}
TESTS = ["tests/test_foo.py"]
AXES = {"conformance": "pass"}
AUDITED = "2026-09-03T00:00:00Z"


def _current_record(sha: str) -> dict:
    return {
        "module": "app/foo.py",
        "source_sha": sha,
        "toolset_version": 1,
        "contract_version": sl.CONTRACT_VERSION,
        "state": "sharpened",
        "audited": AUDITED,
        "axes": AXES,
        "axes_not_applied": [],
    }


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src/saitenka/app").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/saitenka/app/foo.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "src/saitenka/app/bar.py").write_text("Y = 2\n", encoding="utf-8")
    (tmp_path / "tests/test_foo.py").write_text(
        "from saitenka.app.foo import X\n\ndef test_x():\n    assert X == 1\n", encoding="utf-8"
    )
    return tmp_path


def _ledger(root: Path, records: list[dict]) -> sl.Ledger:
    p = root / ".ledger.sharpen.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return sl.Ledger.load(p)


def test_source_sha_is_deterministic_for_same_bytes(tmp_path):
    root = _repo(tmp_path)
    assert sl.source_sha(root, "app/foo.py", TESTS) == sl.source_sha(root, "app/foo.py", TESTS)


def test_source_sha_invalidates_on_a_module_edit(tmp_path):
    root = _repo(tmp_path)
    before = sl.source_sha(root, "app/foo.py", TESTS)
    (root / "src/saitenka/app/foo.py").write_text("X = 2\n", encoding="utf-8")
    assert sl.source_sha(root, "app/foo.py", TESTS) != before


def test_source_sha_invalidates_on_a_test_edit(tmp_path):
    root = _repo(tmp_path)
    before = sl.source_sha(root, "app/foo.py", TESTS)
    (root / "tests/test_foo.py").write_text("# edited\n", encoding="utf-8")
    assert sl.source_sha(root, "app/foo.py", TESTS) != before


def test_status_is_unseen_without_a_record(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    assert ledger.status("app/foo.py", root, TESTS) == sl.UNSEEN


def test_status_is_sharpened_current_when_sharpened_and_unchanged(tmp_path):
    root = _repo(tmp_path)
    sha = sl.source_sha(root, "app/foo.py", TESTS)
    rec = _current_record(sha)
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.status("app/foo.py", root, TESTS) == sl.SHARPENED_CURRENT


def test_status_goes_stale_when_the_source_is_edited(tmp_path):
    root = _repo(tmp_path)
    sha = sl.source_sha(root, "app/foo.py", TESTS)
    rec = _current_record(sha)
    ledger = _ledger(root, [MANIFEST, rec])
    (root / "src/saitenka/app/foo.py").write_text("X = 99\n", encoding="utf-8")
    assert ledger.status("app/foo.py", root, TESTS) == sl.STALE_SHA


def test_status_goes_stale_when_the_toolset_version_bumps(tmp_path):
    root = _repo(tmp_path)
    sha = sl.source_sha(root, "app/foo.py", TESTS)
    rec = {"module": "app/foo.py", "source_sha": sha, "toolset_version": 1, "state": "sharpened"}
    ledger = _ledger(root, [{"type": "manifest", "toolset_version": 2}, rec])
    assert ledger.status("app/foo.py", root, TESTS) == sl.STALE_TOOLSET


def test_status_goes_stale_when_the_contract_changes(tmp_path):
    root = _repo(tmp_path)
    sha = sl.source_sha(root, "app/foo.py", TESTS)
    rec = {
        "module": "app/foo.py",
        "source_sha": sha,
        "toolset_version": 1,
        "contract_version": sl.CONTRACT_VERSION - 1,
        "state": "sharpened",
    }
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.status("app/foo.py", root, TESTS) == sl.STALE_CONTRACT


def test_current_contract_without_axis_evidence_cannot_suppress_a_module(tmp_path):
    root = _repo(tmp_path)
    sha = sl.source_sha(root, "app/foo.py", TESTS)
    rec = _current_record(sha)
    rec.pop("axes_not_applied")
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.status("app/foo.py", root, TESTS) == sl.STALE_CONTRACT


def test_latest_returns_the_most_recent_record(tmp_path):
    root = _repo(tmp_path)
    old = {"module": "app/foo.py", "source_sha": "aaa", "state": "in-progress"}
    new = {"module": "app/foo.py", "source_sha": "bbb", "state": "sharpened"}
    ledger = _ledger(root, [MANIFEST, old, new])
    assert ledger.latest("app/foo.py")["source_sha"] == "bbb"


def test_grow_filed_maps_module_to_open_issue_refs(tmp_path):
    root = _repo(tmp_path)
    rec = {"module": "app/foo.py", "source_sha": "x", "state": "in-progress", "grow-filed": ["#43"]}
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.grow_filed() == {"app/foo.py": ["#43"]}


def test_append_round_trips_a_record(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    ledger.append({"module": "app/foo.py", "source_sha": "z", "state": "sharpened"})
    assert sl.Ledger.load(root / ".ledger.sharpen.jsonl").latest("app/foo.py")["source_sha"] == "z"


def test_map_tests_to_modules_keeps_every_directly_imported_module(tmp_path):
    root = _repo(tmp_path)
    # imports foo twice, bar once → maps to the module it leans on most
    (root / "tests/test_mix.py").write_text(
        "from saitenka.app.foo import X\nfrom saitenka.app.foo import X as Y\n"
        "from saitenka.app.bar import Y as Z\n\ndef test_z():\n    assert X == Z\n",
        encoding="utf-8",
    )
    mapping = sl.map_tests_to_modules(root)
    assert "tests/test_mix.py" in mapping["app/foo.py"]
    assert "tests/test_mix.py" in mapping["app/bar.py"]


def test_attribution_is_function_level_and_evidence_bearing(tmp_path):
    root = _repo(tmp_path)
    # test_bar.py leans on foo (imported twice) but its stem names bar → must map to bar, not foo
    (root / "tests/test_bar.py").write_text(
        "from saitenka.app.foo import X\nfrom saitenka.app.bar import Y as Z\n\n"
        "def test_bar():\n    assert Z == 2\n",
        encoding="utf-8",
    )
    edges = [e for e in sl.test_attributions(root) if e.test_file == "tests/test_bar.py"]
    assert any(e.module == "app/bar.py" and e.function == "test_bar" for e in edges)
    assert any(e.module == "app/foo.py" and not e.high_confidence for e in edges)


def test_status_is_stale_when_the_module_moved_instead_of_raising(tmp_path):
    root = _repo(tmp_path)
    sha = sl.source_sha(root, "app/foo.py", TESTS)
    rec = _current_record(sha)
    ledger = _ledger(root, [MANIFEST, rec])
    (root / "src/saitenka/app/foo.py").unlink()  # module moved/deleted
    assert ledger.status("app/foo.py", root, TESTS) == sl.STALE_SHA  # re-audit, never crash


def test_prepare_record_owns_identity_and_requires_axis_reflection(tmp_path):
    import pytest

    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    record = {
        "module": "app/foo.py",
        "tests": TESTS,
        "state": "dry-run",
        "audited": "2026-09-03T00:00:00Z",
        "axes": {"conformance": "pass"},
    }
    with pytest.raises(ValueError, match="axes_not_applied"):
        sl.prepare_record(record, root, ledger)
    prepared = sl.prepare_record({**record, "axes_not_applied": ["efficacy: no DB"]}, root, ledger)
    assert prepared["source_sha"] == sl.source_sha(root, "app/foo.py", TESTS)
    assert prepared["contract_version"] == sl.CONTRACT_VERSION


def test_outer_reflection_becomes_due_and_human_receipt_resets_it(tmp_path):
    root = _repo(tmp_path)
    records = [
        MANIFEST,
        *(
            {"module": f"app/{index}.py", "source_sha": str(index), "state": "dry-run"}
            for index in range(sl.OUTER_REFLECTION_CADENCE)
        ),
    ]
    ledger = _ledger(root, records)
    assert ledger.outer_reflection_due()
    reflection = sl.prepare_outer_reflection(
        {
            "date": "2026-09-03",
            "findings": ["f"],
            "next": ["n"],
            "toolset_decision": "keep v1",
            "human_decision": "accepted",
        },
        ledger,
    )
    ledger.append(reflection)
    assert not ledger.outer_reflection_due()
