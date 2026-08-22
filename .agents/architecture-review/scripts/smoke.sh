#!/usr/bin/env bash
# Smoke: the loop's own artifacts must exist and census.json must parse, or the "delta between two
# runs" the architecture-review skill is built on has nowhere to live.
# test -f/-e and a json parse only — grep-free, safe under the search-shim mock (see AGENTS.md).
set -euo pipefail
loop_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$loop_dir/SPEC.md"
test -f "$loop_dir/census.json"
test -d "$loop_dir/runs"
cd "$loop_dir/../.."   # -> repo root
test -f .agents/skills/architecture-review/SKILL.md   # the loop is process; the skill is judgement
uv run python - "$loop_dir/census.json" <<'PY'
import json, sys

data = json.loads(open(sys.argv[1], encoding="utf-8").read())
claims, censused = data["claims"], set(data["censused"])
valid = {"gated", "tested", "argued"}
for c in claims:
    assert c["status"] in valid, f"{c['id']}: bad status {c['status']}"
    # An argued claim without a remedy is a shrug; the whole point is that it names the next step.
    assert c["status"] != "argued" or c["settles"], f"{c['id']}: argued with no `settles`"
    assert c["module"] in censused or not c["module"].startswith("src/"), (
        f"{c['id']}: claims a module absent from `censused` ({c['module']})"
    )
argued = sum(c["status"] == "argued" for c in claims)
print(f"claim-census: OK {len(censused)} modules censused, {len(claims)} claims, {argued} argued")
PY
echo "architecture-review loop smoke OK"
