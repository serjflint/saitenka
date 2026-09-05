#!/bin/sh
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
root=$(CDPATH= cd -- "$here/../.." && pwd)

for path in \
  "$here/SPEC.md" \
  "$root/.agents/skills/grow-loop/SKILL.md" \
  "$root/tools/grow_gate.py" \
  "$root/tools/grow_ledger.py" \
  "$root/tools/grow_triage.py" \
  "$root/tool_tests/test_grow_gate.py" \
  "$root/tool_tests/test_grow_ledger.py" \
  "$root/tool_tests/test_grow_triage.py"
do
  test -f "$path"
done

uv run python - "$here/SPEC.md" "$root/.agents/skills/grow-loop/SKILL.md" <<'PY'
from pathlib import Path
import sys

spec, skill = (Path(path).read_text(encoding="utf-8") for path in sys.argv[1:])
for token in ("additive", "old-survives", "two isolated", "contribution gates", "status", "Reflection"):
    assert token in spec or token in skill, token
for retired in ("grow_reflect.py", "reflection_id", "trace_sha", "harness.js", "contracts.json"):
    assert retired not in spec
    assert retired not in skill
PY

echo "grow skill smoke: ok"
