#!/usr/bin/env bash
# Smoke: every path and task the skill routes to must still exist, or the review sends its reviewer
# nowhere and it silently degrades into an opinion.
# test -f/-e only — grep-free, safe under the search-shim mock (see AGENTS.md "Tooling").
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
test -f "$skill_dir/SKILL.md"
test -f "$skill_dir/references/axes.md"
test -f "$skill_dir/references/evidence.md"
test -f "$skill_dir/references/claim-classes.md"
cd "$skill_dir/../../.."   # -> repo root
fail=0
have() { test -e "$1" || { echo "MISSING: $1"; fail=1; }; }
have tools/arch_map.py             # four of the ten axes come from this one view
have tools/cluster_map.py          # what a module touches on the host, by fact
have tools/host_mass.py            # retirement, against which "the migration finished" is checked
have tools/host_arity.py           # host coupling, the debt side of the same question
have tools/port_probe_census.py    # a meter the review is told to read before quoting
have tools/reducer_purity.py       # the meter scoped narrower than its name
have BENCHMARKS.md                 # where a performance claim is cited from
have ARCHITECTURE.md               # the static view the review judges against the code
have docs/contributing/runtime.md  # the invariant page — declared, ungated, and to be distrusted
have pyproject.toml                # [tool.poe.tasks]: whether a rule is enforced or only declared
have README.md                     # §0's yardstick: the reviewer derives the product's goals from it
have .agents/rules/searching.md    # the shim ban §0 must restate, or the reviewer fork-bombs the box
have .agents/architecture-review/SPEC.md     # where a run's report and the census live between runs
have .agents/architecture-review/census.json # the agenda §3.1 hands over
have .agents/skills/plan-migration/SKILL.md  # handoff for a finding about a conversion
have .agents/skills/contribute/SKILL.md      # handoff for a finding driven to a PR
if [ "$fail" -eq 0 ]; then echo "architecture-review smoke OK"; else echo "architecture-review smoke FAILED"; exit 1; fi
