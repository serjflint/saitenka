#!/usr/bin/env bash
# Smoke: the skill's structure holds and everything SKILL.md points at still exists.
# Grep-free on purpose — grep/find are PATH-shimmed to a fork-bomb on this machine
# (see AGENTS.md "Tooling"); the assertions run in pure python3.
set -euo pipefail
skill_dir="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
repo_root="$(CDPATH='' cd -- "$skill_dir/../../.." && pwd)"

python3 - "$skill_dir" "$repo_root" <<'PY'
import sys, pathlib, re

skill, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
fail = []
def need(cond, msg):
    if not cond:
        fail.append(msg)

sk = skill / "SKILL.md"
need(sk.is_file(), "SKILL.md missing")
text = sk.read_text() if sk.is_file() else ""

m = re.search(r"^name:\s*(\S+)", text, re.M)
need(bool(m) and m.group(1) == "contribute", "frontmatter name must be 'contribute'")

dm = re.search(r"description:\s*>-\n(.*?)\nmetadata:", text, re.S)
need(bool(dm), "description block not found")
if dm:
    desc = " ".join(line.strip() for line in dm.group(1).splitlines())
    need(len(desc) <= 1024, f"description too long: {len(desc)} > 1024")
    need("<" not in desc and ">" not in desc, "description must not contain angle brackets")
    need(all(s in desc for s in ("write-test", "dev-gate", "sharpen-loop")),
         "description lost the negative cut vs write-test/dev-gate/sharpen-loop")

for anchor in ("Restore context", "root cause", "PoC that proves tractability",
               "adversarial review is a gate", "issue-first"):
    need(anchor in text, f"SKILL.md lost phase anchor: {anchor!r}")

ref = skill / "references" / "review-gate.md"
need(ref.is_file(), "references/review-gate.md missing")
if ref.is_file():
    raw = ref.read_text()
    r = raw.lower()
    need("evidence record" in r and "adversarial" in r,
         "review-gate.md is not the generalized gate (evidence record / adversarial patterns)")
    need("SSOT:" in raw, "review-gate.md lost its vendoring-provenance marker (SSOT:)")

need((repo / "CONTRIBUTING.md").is_file(), "repo CONTRIBUTING.md missing (SKILL.md links it)")
need((repo / "AGENTS.md").is_file(), "AGENTS.md missing (SKILL.md links it)")
for sib in ("write-test", "dev-gate", "sharpen-loop"):
    need((repo / ".agents" / "skills" / sib).is_dir(), f"neighbor skill missing: {sib}")

if fail:
    print("contribute smoke FAILED")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("contribute smoke OK")
PY
