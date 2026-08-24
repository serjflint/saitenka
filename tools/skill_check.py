"""Validate the repository-local skill discovery contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents" / "skills"
_KEBAB = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _frontmatter(path: Path) -> tuple[list[str], list[str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return [], [f"{path}: frontmatter must start with ---"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return [], [f"{path}: frontmatter has no closing ---"]
    return lines[1:end], []


def _document(path: Path, lines: list[str]) -> tuple[dict[str, object], list[str]]:
    try:
        document = yaml.safe_load("\n".join(lines))
    except yaml.YAMLError as error:
        return {}, [f"{path}: invalid YAML frontmatter: {error}"]
    if not isinstance(document, dict):
        return {}, [f"{path}: frontmatter must be a mapping"]
    return document, []


def skill_failures(path: Path) -> list[str]:
    lines, failures = _frontmatter(path)
    if failures:
        return failures

    document, failures = _document(path, lines)
    if failures:
        return failures

    directory = path.parent.name
    name = document.get("name")
    description = document.get("description")
    metadata = document.get("metadata")
    project = metadata.get("project") if isinstance(metadata, dict) else None

    if not _KEBAB.fullmatch(directory):
        failures.append(f"{path}: directory name must be kebab-case: {directory!r}")
    if name != directory:
        failures.append(f"{path}: name must match directory {directory!r}, got {name!r}")
    if isinstance(name, str) and len(name) > 64:
        failures.append(f"{path}: name is {len(name)} chars; maximum is 64")
    if not isinstance(description, str):
        failures.append(f"{path}: description is required")
    else:
        if len(description) > 1024:
            failures.append(f"{path}: description is {len(description)} chars; maximum is 1024")
        if "<" in description or ">" in description:
            failures.append(f"{path}: description must not contain angle brackets")
    if project != "saitenka":
        failures.append(f"{path}: metadata.project must be 'saitenka', got {project!r}")
    return failures


def check_skills(skill_root: Path = SKILLS) -> list[str]:
    failures: list[str] = []
    for directory in sorted(path for path in skill_root.iterdir() if path.is_dir()):
        skill = directory / "SKILL.md"
        if not skill.is_file():
            failures.append(f"{directory}: skill directory has no SKILL.md")
            continue
        failures.extend(skill_failures(skill))
    return failures


def main() -> int:
    failures = check_skills()
    if failures:
        print("skill contract FAILED", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("skill contract OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
