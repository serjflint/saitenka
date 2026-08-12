---
name: agents-md
description: >-
  Decide where an agent-facing convention or constraint belongs, and keep AGENTS.md minimal — routing
  across saitenka's config surfaces (AGENTS.md, .agents/rules/, .agents/skills/, .agents/hooks/, poe
  required-vs-advisory gates, .agents/mcp/servers.json). Use when editing AGENTS.md or adding a
  rule/skill/hook/gate, or asking "should this go in AGENTS.md or a rule/skill/hook?", "is this line worth
  keeping?", "where does this convention live?", "is this a rule or a skill?". Applies the inclusion test
  (non-inferable + stable + not-enforceable-elsewhere), the persist-as-what call (rule vs skill vs hook vs
  gate vs loop), and keep-it-fresh review; ships its own vendored guide + evidence in references/
  (self-contained). NOT for turning the intelligence stack on (use agent-setup); NOT for which tool answers
  a question at runtime (use agent-tooling); NOT for writing a test (use write-test).
metadata:
  project: saitenka
---

# agents-md

Procedure for authoring/curating saitenka's agent-facing config so a convention lands in the **right home**
and AGENTS.md stays **minimal**. The full evidence-based guide is vendored, self-contained, in
[`references/writing-agents-md.md`](references/writing-agents-md.md); this file is the decision procedure.

## The one rule: keep AGENTS.md minimal

Every hardcoded line is something that can **contradict reality** — a hook, MCP server, newer tool version,
or pluggable backend may provide it differently, and agents follow written instructions *literally*, so a
stale line steers them wrong confidently. Two measured reasons reinforce it: context length itself degrades
the model every session, and compliance decays *within* a session as code is generated (a static file can't
fix that — enforce at the moment of action instead). Rule of thumb: a few dozen high-value lines; past a
screenful, content is in the wrong home → route it. saitenka already encodes the *prose* half of this in
AGENTS.md "Comments"/"Documentation" (one-canonical-source, information-delta-only); this skill is the
**routing** half.

## Does a line earn its place? (inclusion test — all three)

1. **Non-inferable** — the model can't know it from public training. `git`/`gh`, Python, "clean code" are
   inferable → don't state them; a saitenka footgun, a codegen boundary, a domain invariant a public model
   would "fix" as a bug are not.
2. **Stable & universal** — true repo-wide, not environment/config-dependent. A *pluggable/optional* choice
   fails this → route to a rule/preset, not the always-loaded file.
3. **Not enforceable elsewhere** — if a linter, type-checker, CI job, hook, or poe gate can guarantee it,
   let it. Prose is an unenforced suggestion; a check is a guarantee.

A good line is all three **with its why**. When in doubt, leave it out — add the line the day an agent
actually gets it wrong.

## Where each candidate goes (routing — bias: enforceable > written)

| Candidate | Home in saitenka |
|-----------|------------------|
| style / format / naming | ruff `select` + formatter (`poe lint`) |
| a hard checkable ban (shell-search, a forbidden import, editing a generated dir) | a **PreToolUse hook** (`.agents/hooks/`, e.g. `block-shell-search.py`) and/or ast-grep (`poe invariants`) / import-linter (`poe arch`) |
| a deterministic pass/fail check | a **poe gate** — required in `poe all` if fast+clean, else advisory (SSOT: `[tool.poe.tasks]`) |
| a "how to do X" recipe/procedure | a **skill** (`.agents/skills/`) — loads on demand, doesn't tax every session |
| deep reference / architecture / rationale | docs site / `README` / `ARCHITECTURE.md`, linked |
| *which* pluggable/optional tool to use | a **path-scoped rule** (`.agents/rules/`) |
| live API/config the agent queries | an **MCP server** (`.agents/mcp/servers.json`) |

A prose rule a hook/lint/gate *could* enforce → convert it.

## persist-as-what (the recurring call)

- **rule** (`.agents/rules/`) — a short, hard, always-on standing constraint; a line never to cross, no procedure.
- **skill** (`.agents/skills/`) — a recurring *procedure* with judgment; loads on demand.
- **hook** (`.agents/hooks/`) — an enforceable ban *at the moment of action* (PreToolUse).
- **gate** (poe task) — a deterministic pass/fail *measurement*; required (`all`) vs advisory.
- **loop** (`.agents/{sharpen,grow}/`) — an idle-time, cross-cutting *quality engine*; never a per-edit rule.

Hard-line-only → rule · a procedure → skill · enforceable-at-action → hook · a pass/fail check → gate · an
idle cross-cutting engine → loop. (Mirrors the `contribute` skill's persist-as-what judgment.)

## Keep it fresh — a stale line is worse than none

- Review AGENTS.md / a rule in the **same PR** that changes the convention it describes.
- A skill ships `scripts/smoke.sh` that rot-guards every path/symbol/reference it cites — **grep-free**
  (`test -f`/`test -e`): `grep`/`find` fork-bomb the search-shim here (AGENTS.md "Tooling").
- Prose minimality is a **review discipline, not a poe gate** (saitenka's stated stance) — but a path or
  command a line names *can* be smoke-checked.
- `.agents/` is durable-only; scratch → `vibe/` (git-ignored).

## saitenka specifics (don't relitigate)

- VCS is **git + gh** (public repo) — never internal-VCS framing; public tooling is valid and inferable.
- New skills are **self-contained**: ship your own reference copies, don't depend on another skill being
  installed. The **description is the trigger** — concrete phrases + a negative cut vs neighbors, ≤1024
  chars, no angle brackets; `name` kebab-case ≤64.
- Claude Code discovers skills via the git-ignored `.claude/skills -> ../.agents/skills` symlink (**per
  worktree** — a fresh worktree has none; `ln -s ../.agents/skills .claude/skills`); Codex reads
  `.agents/skills/` directly.

## Verify

`bash scripts/smoke.sh` (grep-free — safe to run here).
