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

# The oracle-catalog reference the skill points at, and the canonical oracle examples it cites (test -f is
# grep-free — safe under the search-shim mock; see AGENTS.md "Tooling").
test -f "$skill_dir/references/oracle-catalog.md" || { echo "MISSING: references/oracle-catalog.md"; fail=1; }
for t in test_scale_boundary test_tooltip_statemachine test_cache_race test_cache_equivalence test_crisp_scale_properties; do
  test -f "$OV/tests/$t.py" || { echo "MISSING oracle example: tests/$t.py"; fail=1; }
done
test -f "$OV/tests/util.py" || { echo "MISSING: tests/util.py (PROFILES matrix)"; fail=1; }
# The test-live task the Verify step invokes
grep -q "test-live" "$OV/pyproject.toml" || { echo "MISSING task: test-live"; fail=1; }

if [ "$fail" -eq 0 ]; then echo "write-test smoke OK"; else echo "write-test smoke FAILED"; exit 1; fi
