#!/usr/bin/env bash
# Smoke: the research skill's structure holds and its pointers exist. Grep-free
# (grep/find are PATH-shimmed to a fork-bomb here; see AGENTS.md "Tooling").
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
need(bool(m) and m.group(1) == "research", "frontmatter name must be 'research'")

dm = re.search(r"description:\s*>-\n(.*?)\nmetadata:", text, re.S)
need(bool(dm), "description block not found")
if dm:
    desc = " ".join(line.strip() for line in dm.group(1).splitlines())
    need(len(desc) <= 1024, f"description too long: {len(desc)} > 1024")
    need("<" not in desc and ">" not in desc, "description must not contain angle brackets")
    need(all(s in desc for s in ("repowise", "WebFetch", "contribute")),
         "description lost the negative cut (repowise / WebFetch / contribute)")

for anchor in ("Assemble context", "Author + sharpen", "Handoff", "Verify — the gate",
               "Triage the signals", "Widen", "Deepen", "Sharpen", "open it in VS Code",
               "Aggregate", "Caveats", "fact-check", "maintenance status"):
    need(anchor in text, f"SKILL.md lost anchor: {anchor!r}")
need("**Do**" in text and "**Don't**" in text, "SKILL.md lost the dos/don'ts split")

need("<role>" in text and "XML-tagged" in text, "SKILL.md lost the role / XML-marker prompt convention")

kit = skill / "references" / "prompt-kit.md"
need(kit.is_file(), "references/prompt-kit.md missing")
if kit.is_file():
    k = kit.read_text()
    for tag in ("<role>", "<context>", "<verification_rules>", "<output_format>"):
        need(tag in k, f"prompt-kit.md lost the {tag} skeleton marker")
    need("maintenance status" in k and "confirmed: link" in k and "gh search repos" in k,
         "prompt-kit.md lost the output contract / tagged-claim / grounded-discovery guidance")

need("AGENTS.md" in text and "Tooling" in text, "SKILL.md lost the AGENTS.md Tooling/searching pointer")
need((repo / "AGENTS.md").is_file(), "AGENTS.md missing (SKILL.md links its Tooling rule)")

# the grounded-verification helper: exists, is valid python, is grep-free, and SKILL points at it
vfy = skill / "scripts" / "verify.py"
need(vfy.is_file(), "scripts/verify.py missing")
need("verify.py" in text, "SKILL.md lost the scripts/verify.py pointer")
if vfy.is_file():
    src = vfy.read_text()
    try:
        import ast
        ast.parse(src)
    except SyntaxError as exc:
        need(False, f"verify.py does not parse: {exc}")
    need('"gh"' in src and "ABANDONED_DAYS" in src, "verify.py lost its gh/abandoned grounding")
    # invocation check (quoted subprocess args), not prose — the docstring may name grep/find
    for shimmed in ('"grep"', '"find"', '"rg"', '"ripgrep"', '"pgrep"'):
        need(shimmed not in src, f"verify.py must not invoke shimmed {shimmed}")

if fail:
    print("research smoke FAILED")
    for f in fail:
        print("  -", f)
    sys.exit(1)
print("research smoke OK")
PY
