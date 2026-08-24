---
name: skill-creator
description: >-
  Author a new saitenka .agents/skills skill well, once a skill is the right home — the structure
  (SKILL.md procedure + references/ depth + grep-free scripts/smoke.sh + optional agents/openai.yaml),
  a discovery-optimized trigger description, progressive disclosure, and a rot-guard smoke. Use when
  asked to "write/create a new skill", "author a skill", "turn this procedure into a skill", "add a
  SKILL.md", "make an agent skill for X", or "scaffold a skill". Applies the description-is-the-trigger
  rule, the hard constraints (description at most 1024 chars and no angle brackets; name kebab-case at
  most 64), self-contained vendoring, and verify-with-smoke. NOT for deciding WHERE a convention belongs
  (rule vs skill vs hook vs gate — use agents-md); NOT for turning the tooling on (use agent-setup) or
  picking a runtime code-intel tool (use agent-tooling); NOT for writing a test (use write-test).
metadata:
  project: saitenka
---

# skill-creator

Given that **a skill is the answer**, author it well. The full best-practice checklist and the hard
constraints are vendored, self-contained, in
[`references/authoring-skills.md`](references/authoring-skills.md); this file is the procedure.

## Boundary vs agents-md

[[agents-md]] decides *where a convention belongs* — rule vs skill vs hook vs gate vs loop (the
persist-as-what call). This skill starts one step later: the routing already said **skill**, now build
it right. Route the "should this even be a skill?" question there first; don't relitigate it here.

## Skill anatomy

A skill is a directory under `.agents/skills/<name>/`:

- **`SKILL.md`** — frontmatter (`name`, `description`, `metadata.project: saitenka`) + a **short**
  procedure. Aim ~60–110 lines; this is what loads on trigger.
- **`references/`** — the depth (checklists, tables, rationale). Loaded only when the skill runs, so
  cost lands on demand, not every session.
- **`scripts/smoke.sh`** — a **grep-free** rot-guard (`test -f`/`test -e` only — `grep`/`find`
  fork-bomb this env's search-shim; see AGENTS.md "Tooling").
- **`agents/openai.yaml`** (optional) — the Codex descriptor (`display_name`, `short_description`,
  `default_prompt`).

Discovery differs by agent: Claude Code reads skills through the git-ignored
`.claude/skills -> ../.agents/skills` symlink — **per worktree**, so a fresh worktree has none
(`ln -s ../.agents/skills .claude/skills` to wire it); Codex reads `.agents/skills/` directly.

## The description IS the trigger

The model sees only `name` + `description` until the skill fires, so the description does all the
discovery work. Write it in three moves:

1. **First clause = the capability** — what the skill does, in the user's words.
2. **Concrete trigger phrases** — the real things a user says ("run the gate", "write a test for X"),
   not abstract category names.
3. **An explicit negative cut** — "NOT for X → use Y" against each neighboring skill, so overlapping
   skills don't both fire (or both stay silent).

Be **slightly pushy**: models under-trigger, so err toward claiming the ground. Hard limits: description
**at most 1024 chars, no angle brackets** (`<`/`>` break the parse); `name` **kebab-case, at most 64**.

## Progressive disclosure

Keep `SKILL.md` short — it is loaded whenever the skill fires. Push deep detail to `references/` and
deterministic steps to `scripts/`. A long SKILL.md is the smell: split it, don't pad it.

## Self-contained + design-for-reuse

- **Vendor your references.** Don't depend on another skill being installed — copy the guide you need
  under `references/` (as agents-md and contribute do).
- **No hardcoded IDs.** Take the target/name/config as `$ARGUMENTS` or read it; a skill that only works
  for one path isn't a skill.
- **Credit external guidance.** Where the advice comes from Anthropic, cite it in the references (not
  the SKILL.md body) — see `references/authoring-skills.md`.

## Verify

`bash scripts/smoke.sh` asserts the skill's own files plus the sibling surfaces it names still exist —
paths and structure, **never** agent prose (behavior isn't smoke-checkable). Beyond the smoke, sanity-
check the trigger with a couple of examples. `poe skill-contract` is the repo-wide discovery gate: it
checks name/path agreement, kebab-case, the description parser limits, and `metadata.project` for every skill.

- **Should fire:** "create a skill that runs the release checklist."
- **Should NOT fire:** "should this go in AGENTS.md or a hook?" → that's [[agents-md]].
