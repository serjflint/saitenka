from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CHECKER = Path(__file__).parents[1] / "tools" / "skill_check.py"


def _module():
    spec = importlib.util.spec_from_file_location("_skill_check", _CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_skill(
    root: Path,
    name: str,
    description: str,
    *,
    declared_name: str | None = None,
    marker: str = ">-",
) -> Path:
    directory = root / name
    directory.mkdir()
    path = directory / "SKILL.md"
    path.write_text(
        "---\n"
        f"name: {declared_name or name}\n"
        f"description: {marker}\n"
        f"  {description}\n"
        "metadata:\n"
        "  project: saitenka\n"
        "---\n",
        encoding="utf-8",
    )
    return path


def test_real_skill_frontmatter_is_valid() -> None:
    assert _module().check_skills() == []


def test_valid_fixture_passes(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample-skill", "Do one bounded thing.")
    assert checker.skill_failures(skill) == []


def test_description_length_limit_is_live(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample-skill", "x" * 1025)
    assert any("maximum is 1024" in failure for failure in checker.skill_failures(skill))


def test_description_length_cannot_hide_behind_keep_block_marker(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample-skill", "x" * 1025, marker="|+")
    assert any("maximum is 1024" in failure for failure in checker.skill_failures(skill))


def test_keep_block_marker_counts_its_retained_newline(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample-skill", "x" * 1024, marker="|+")
    assert any("1025 chars" in failure for failure in checker.skill_failures(skill))


def test_description_rejects_angle_brackets(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample-skill", "Do <one> thing.")
    assert any("angle brackets" in failure for failure in checker.skill_failures(skill))


def test_name_must_match_kebab_case_directory(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample_skill", "Do one thing.", declared_name="other")
    failures = checker.skill_failures(skill)
    assert any("directory name must be kebab-case" in failure for failure in failures)
    assert any("name must match directory" in failure for failure in failures)


def test_name_length_boundary_is_live(tmp_path: Path) -> None:
    checker = _module()
    valid = _write_skill(tmp_path, "a" * 64, "Do one thing.")
    invalid = _write_skill(tmp_path, "b" * 65, "Do one thing.")
    assert checker.skill_failures(valid) == []
    assert any("maximum is 64" in failure for failure in checker.skill_failures(invalid))


def test_metadata_project_is_required(tmp_path: Path) -> None:
    checker = _module()
    skill = _write_skill(tmp_path, "sample-skill", "Do one thing.")
    skill.write_text(
        skill.read_text(encoding="utf-8").replace("saitenka", "another"), encoding="utf-8"
    )
    assert any("metadata.project" in failure for failure in checker.skill_failures(skill))
