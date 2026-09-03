#!/usr/bin/env bash
set -euo pipefail

skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_dir="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"

test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/agents/openai.yaml"
test -f "$repo_dir/.agents/sharpen/SPEC.md"
test -f "$repo_dir/.agents/sharpen/ADAPTERS.md"
test -f "$repo_dir/.agents/sharpen/PROMPTS.md"
test -f "$repo_dir/.agents/sharpen/contracts.json"
test -f "$repo_dir/.agents/skills/assurance-pipeline/SKILL.md"

uv run python - "$repo_dir" <<'PY'
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
skill = (root / ".agents/skills/sharpen-loop/SKILL.md").read_text()
harness = (root / ".agents/sharpen/harness.js").read_text()
contracts = json.loads((root / ".agents/sharpen/contracts.json").read_text())
assert "TODO" not in skill and "[TODO" not in skill
assert 'fork_turns="none"' in skill
assert contracts["version"] == 6 and {"select", "ship_gate", "record"} <= contracts.keys()
assert contracts["lifecycle"]["outer_reflection_cadence"] == 3
assert "CONTRACT_VERSION = 6" in harness
assert "reflection-status" in harness and "recorded_axes_not_applied" in harness
for token in ("better_fix", "Better fix hand-off", "skeptic_verdict", "judge_verdict", "uv run poe all"):
    assert token in harness
assert re.search(r"verdict = judge\?\.verdict === 'UPHELD' \? 'UPHELD' : 'REFUTED'", harness)
assert "model:" not in harness
PY

node "$repo_dir/.agents/sharpen/test_harness.mjs"

echo "sharpen-loop skill smoke: ok"
