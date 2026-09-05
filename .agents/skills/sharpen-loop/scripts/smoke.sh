#!/bin/sh
set -eu

skill_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
repo_dir=$(CDPATH= cd -- "$skill_dir/../../.." && pwd)

for path in \
  "$repo_dir/.agents/sharpen/SPEC.md" \
  "$skill_dir/SKILL.md" \
  "$repo_dir/tools/sharpen_gate.py" \
  "$repo_dir/tools/sharpen_ledger.py" \
  "$repo_dir/tools/sharpen_triage.py" \
  "$repo_dir/tool_tests/test_sharpen_gate.py" \
  "$repo_dir/tool_tests/test_sharpen_ledger.py" \
  "$repo_dir/tool_tests/test_sharpen_triage.py"
do
  test -f "$path"
done

uv run python - "$repo_dir/.agents/sharpen/SPEC.md" "$skill_dir/SKILL.md" <<'PY'
from pathlib import Path
import sys

spec, skill = (Path(path).read_text(encoding="utf-8") for path in sys.argv[1:])
for token in ("existing test", "preservation witness", "two isolated", "contribution gates", "Reflection"):
    assert token in spec or token in skill, token
for retired in ("sharpen_policy.py", "policy.json", "harness.js", "contracts.json"):
    assert retired not in spec
    assert retired not in skill
PY

echo "sharpen skill smoke: ok"
