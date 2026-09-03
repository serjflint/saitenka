#!/usr/bin/env bash
# Rot-guard for the .agents/grow/ loop artifacts. Grep-free on purpose — grep/find/rg are PATH-shimmed to
# a fork-bomb mock in this env (see .agents/rules/searching.md); all text checks go through python.
# Run: bash .agents/grow/scripts/smoke.sh   (from the repository root)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # .agents/grow
root="$(cd "$here/../.." && pwd)"

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }

# 1. All six artifacts + this script exist.
for f in SPEC.md GUIDE.md ADAPTERS.md contracts.json PROMPTS.md harness.js test_harness.mjs BACKTEST.md scripts/smoke.sh; do
  [ -f "$here/$f" ] || fail "missing $f"
done

# 2. contracts.json parses and carries the expected top-level schema keys.
uv run python - "$here/contracts.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
need = {"version", "lifecycle", "gap", "test_design", "proposal", "gate", "ship_gate", "review", "review_provenance", "record", "audit_record", "reflection"}
missing = need - set(data)
assert not missing, f"contracts.json missing keys: {missing}"
assert data["lifecycle"]["terminal_phase"] == "Reflect"
PY

# 3. harness.js is syntactically valid (if node is available) and its CONTRACT_VERSION matches contracts.json.
uv run python - "$here/harness.js" "$here/contracts.json" "$root/tools/grow_ledger.py" <<'PY'
import json, re, sys
src = open(sys.argv[1], encoding="utf-8").read()
ver = json.load(open(sys.argv[2], encoding="utf-8"))["version"]
m = re.search(r"CONTRACT_VERSION\s*=\s*(\d+)", src)
assert m, "harness.js has no CONTRACT_VERSION"
assert int(m.group(1)) == ver, f"harness CONTRACT_VERSION {m.group(1)} != contracts.json version {ver}"
ledger = open(sys.argv[3], encoding="utf-8").read()
m = re.search(r"^CONTRACT_VERSION\s*=\s*(\d+)", ledger, re.MULTILINE)
assert m, "grow_ledger.py has no CONTRACT_VERSION"
assert int(m.group(1)) == ver, f"ledger CONTRACT_VERSION {m.group(1)} != contracts.json version {ver}"
for tok in ("grow_triage", "grow_gate", "grow_ledger", "grow_reflect"):
    assert tok in src, f"harness.js never invokes {tok}"
for phase in ("Select", "Test design", "Author", "Objective gate", "Skeptic", "Judge", "Ship gate", "Record", "Reflect"):
    assert phase in src, f"harness.js missing phase {phase!r}"
PY
if command -v node >/dev/null 2>&1; then
  node --check "$here/harness.js" || fail "harness.js failed node --check"
  node "$here/test_harness.mjs" || fail "Grow harness behavior smoke failed"
fi

# 4. The deterministic tools the harness orchestrates exist and their unit tests are present.
for t in grow_gate.py grow_ledger.py grow_triage.py grow_contexts.py grow_reflect.py; do
  [ -f "$root/tools/$t" ] || fail "missing tools/$t"
done
for t in test_grow_gate.py test_grow_ledger.py test_grow_triage.py test_grow_contexts.py test_grow_reflect.py; do
  [ -f "$root/tool_tests/$t" ] || fail "missing tool_tests/$t"
done

# 5. SPEC and GUIDE cross-reference each other (the reader's-guide handshake).
uv run python - "$here/SPEC.md" "$here/GUIDE.md" <<'PY'
import sys
spec = open(sys.argv[1], encoding="utf-8").read()
guide = open(sys.argv[2], encoding="utf-8").read()
assert "GUIDE.md" in spec, "SPEC.md must point to GUIDE.md"
assert "SPEC.md" in guide, "GUIDE.md must point to SPEC.md"
PY

# 6. The Codex adapter carries the machine-declared mandatory terminal phase.
uv run python - "$here/contracts.json" "$root/.agents/skills/grow-loop/SKILL.md" <<'PY'
import json, sys
contract = json.load(open(sys.argv[1], encoding="utf-8"))
skill = open(sys.argv[2], encoding="utf-8").read()
phase = contract["lifecycle"]["terminal_phase"]
marker = f"Mandatory terminal phase — {phase}."
assert marker in skill, f"Codex grow-loop skill missing terminal phase marker: {marker}"
PY

echo "SMOKE OK: .agents/grow/ artifacts consistent"
