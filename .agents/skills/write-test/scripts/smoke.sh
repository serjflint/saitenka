#!/usr/bin/env bash
# Smoke: every symbol/test/marker the SKILL.md points at must still exist, or the skill has rotted.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/agents/openai.yaml"
cd "$skill_dir/../../.."   # -> overlay/ repo root's overlay dir
OV="overlay"
fail=0
need() { grep -rq "$1" "$OV/$2" || { echo "MISSING: $1  (in $2)"; fail=1; }; }

# Canonical example tests referenced by the skill
need "def test_card_data_from_token" tests/test_mining.py
need "def test_build_note_maps_lapis_fields" tests/test_mining.py
need "_FlakyWriteTransport" tests/test_ipc_chaos.py
need "def test_gil_stays_disabled_after_all_imports" tests/test_ft_gil.py

# Real fakes the recipes tell you to use
for f in FakeMpvServer FakeTransport FakeAnki; do need "$f" tests; done

# Assertion + timeout deps must be installed (dirty-equals recipe / timeout marker)
grep -q "dirty-equals" "$OV/pyproject.toml" || { echo "MISSING dep: dirty-equals"; fail=1; }
grep -q "pytest-timeout" "$OV/pyproject.toml" || { echo "MISSING dep: pytest-timeout"; fail=1; }

# Markers the skill names must be the real declared ones
for m in live integration slow requires_display e2e; do
  grep -q "\"$m:" "$OV/pyproject.toml" || { echo "MISSING marker: $m"; fail=1; }
done

if [ "$fail" -eq 0 ]; then echo "write-test smoke OK"; else echo "write-test smoke FAILED"; exit 1; fi
