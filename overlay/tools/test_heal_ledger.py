"""Tests for the test-healing ledger lib. Run explicitly (tools/ is outside `poe all`):
    uv run python -m pytest tools/test_heal_ledger.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import heal_ledger as hl

MANIFEST = {"type": "manifest", "toolset_version": 1}
TESTS = ["tests/test_foo.py"]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src/overlay/app").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/overlay/app/foo.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "src/overlay/app/bar.py").write_text("Y = 2\n", encoding="utf-8")
    (tmp_path / "tests/test_foo.py").write_text(
        "from overlay.app.foo import X\n\ndef test_x():\n    assert X == 1\n", encoding="utf-8"
    )
    return tmp_path


def _ledger(root: Path, records: list[dict]) -> hl.Ledger:
    p = root / ".ledger.healing.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return hl.Ledger.load(p)


def test_source_sha_is_deterministic_for_same_bytes(tmp_path):
    root = _repo(tmp_path)
    assert hl.source_sha(root, "app/foo.py", TESTS) == hl.source_sha(root, "app/foo.py", TESTS)


def test_source_sha_invalidates_on_a_module_edit(tmp_path):
    root = _repo(tmp_path)
    before = hl.source_sha(root, "app/foo.py", TESTS)
    (root / "src/overlay/app/foo.py").write_text("X = 2\n", encoding="utf-8")
    assert hl.source_sha(root, "app/foo.py", TESTS) != before


def test_source_sha_invalidates_on_a_test_edit(tmp_path):
    root = _repo(tmp_path)
    before = hl.source_sha(root, "app/foo.py", TESTS)
    (root / "tests/test_foo.py").write_text("# edited\n", encoding="utf-8")
    assert hl.source_sha(root, "app/foo.py", TESTS) != before


def test_status_is_unseen_without_a_record(tmp_path):
    root = _repo(tmp_path)
    ledger = _ledger(root, [MANIFEST])
    assert ledger.status("app/foo.py", root, TESTS) == hl.UNSEEN


def test_status_is_healed_current_when_healed_and_unchanged(tmp_path):
    root = _repo(tmp_path)
    sha = hl.source_sha(root, "app/foo.py", TESTS)
    rec = {"module": "app/foo.py", "source_sha": sha, "toolset_version": 1, "state": "healed"}
    ledger = _ledger(root, [MANIFEST, rec])
    assert ledger.status("app/foo.py", root, TESTS) == hl.HEALED_CURRENT


def test_status_goes_stale_when_the_source_is_edited(tmp_path):
    root = _repo(tmp_path)
    sha = hl.source_sha(root, "app/foo.py", TESTS)
    rec = {"module": "app/foo.py", "source_sha": sha, "toolset_version": 1, "state": "healed"}
    ledger = _ledger(root, [MANIFEST, rec])
    (root / "src/overlay/app/foo.py").write_text("X = 99\n", encoding="utf-8")
    assert ledger.status("app/foo.py", root, TESTS) == hl.STALE_SHA


def test_status_goes_stale_when_the_toolset_version_bumps(tmp_path):
    root = _repo(tmp_path)
    sha = hl.source_sha(root, "app/foo.py", TESTS)
    rec = {"module": "app/foo.py", "source_sha": sha, "toolset_version": 1, "state": "healed"}
    ledger = _ledger(root, [{"type": "manifest", "toolset_version": 2}, rec])
    assert ledger.status("app/foo.py", root, TESTS) == hl.STALE_TOOLSET


def test_latest_returns_the_most_recent_record(tmp_path):
    root = _repo(tmp_path)
    old = {"module": "app/foo.py", "source_sha": "aaa", "state": "in-progress"}
    new = {"module": "app/foo.py", "source_sha": "bbb", "state": "healed"}
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
    ledger.append({"module": "app/foo.py", "source_sha": "z", "state": "healed"})
    assert hl.Ledger.load(root / ".ledger.healing.jsonl").latest("app/foo.py")["source_sha"] == "z"


def test_map_tests_to_modules_picks_the_dominant_import(tmp_path):
    root = _repo(tmp_path)
    # imports foo twice, bar once → maps to the module it leans on most
    (root / "tests/test_mix.py").write_text(
        "from overlay.app.foo import X\nfrom overlay.app.foo import X as Y\n"
        "from overlay.app.bar import Y as Z\n\ndef test_z():\n    assert True\n",
        encoding="utf-8",
    )
    mapping = hl.map_tests_to_modules(root)
    assert "tests/test_mix.py" in mapping["app/foo.py"]
    assert "tests/test_mix.py" not in mapping.get("app/bar.py", [])


def test_map_tests_to_modules_prefers_the_filename_stem_over_import_count(tmp_path):
    root = _repo(tmp_path)
    # test_bar.py leans on foo (imported twice) but its stem names bar → must map to bar, not foo
    (root / "tests/test_bar.py").write_text(
        "from overlay.app.foo import X\nfrom overlay.app.foo import X as Y\n"
        "from overlay.app.bar import Y as Z\n\ndef test_bar():\n    assert True\n",
        encoding="utf-8",
    )
    mapping = hl.map_tests_to_modules(root)
    assert "tests/test_bar.py" in mapping["app/bar.py"]
    assert "tests/test_bar.py" not in mapping["app/foo.py"]
