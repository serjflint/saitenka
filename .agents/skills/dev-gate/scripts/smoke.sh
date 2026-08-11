#!/usr/bin/env bash
# Structural rot-guard; overlay/pyproject.toml remains the sole task-list authority.
set -euo pipefail

skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_dir="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"

test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"

uv run python - "$repo_dir/overlay/pyproject.toml" <<'PY'
import sys
import tomllib

with open(sys.argv[1], "rb") as stream:
    tasks = tomllib.load(stream)["tool"]["poe"]["tasks"]

assert isinstance(tasks["all"], list), "poe all must remain a task sequence"
assert "loop-tools-test" in tasks["all"], "poe all lost deterministic loop-tool rot-guards"
assert "pre-release" in tasks, "pre-release gate missing"
PY

echo "dev-gate smoke OK"
