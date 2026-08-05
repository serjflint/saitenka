# Authoring a skill — the guide (vendored, self-contained)

Depth for `SKILL.md`. Load only when authoring a skill. Distilled from Anthropic's Agent Skills docs and
reframed for saitenka's `.agents/skills/` layout; vendored here so the skill needs nothing else installed.

> **Sources (Anthropic).** Agent Skills reference: https://code.claude.com/docs/en/skills.md ·
> Agent Skills design philosophy: https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills ·
> Claude Code settings (skill-listing budget): https://code.claude.com/docs/en/settings

## The best-practice checklist

1. **Description-as-trigger.** The model reads only `name` + `description` until the skill fires, so the
   description is the entire discovery signal. Capability first (user's words) → concrete trigger phrases
   → an explicit negative cut vs each neighbor ("NOT for X → use Y"). Be slightly pushy — models
   under-trigger, so claim the ground rather than hedge.
2. **Progressive disclosure.** `SKILL.md` is a short procedure (aim ~100–150 lines, a hard ceiling near
   ~500); everything deep goes to `references/`, everything deterministic to `scripts/`. Cost then lands
   on demand, not on every session that merely lists the skill.
3. **Positive-first instructions.** Lead with what to *do*; keep prohibitions to the few that actually
   bite. A wall of "don't" reads as noise and buries the one that matters.
4. **Self-contained.** Vendor the references the skill relies on; never assume a sibling skill is
   installed. A skill is a portable directory.
5. **Design for reuse.** No hardcoded IDs, paths, or names baked into the body — take them as
   `$ARGUMENTS` or read them from config. A skill that works for exactly one input is a script, not a
   skill.
6. **Verify with a smoke.** Ship `scripts/smoke.sh` that asserts the files and sibling surfaces the skill
   names — a rot-guard, so a moved path fails loudly instead of the skill silently lying. **Grep-free**
   (`test -f`/`test -e`): `grep`/`find` fork-bomb saitenka's search-shim (AGENTS.md "Tooling").

## Hard constraints (verify before shipping)

| Field / limit | Constraint | Failure if violated |
|---|---|---|
| `description` | at most **1024 chars**, **no angle brackets** (`<` / `>`) | over-length is rejected; a `<`/`>` breaks the frontmatter parse |
| `name` | **kebab-case**, at most **64 chars**, matches the directory | mismatch → the skill won't resolve |
| `SKILL.md` length | short procedure (aim ~100–150 lines) | a bloated body taxes every session that lists it |
| `metadata` | `project: saitenka` | keeps the skill scoped to this repo |
| smoke script | `test -f`/`test -e` only | any `grep`/`find` fork-bombs the env |

**The skill-listing budget** (Claude Code `settings`): every installed skill's `name` + `description`
sits in context so the model can choose one. Past a fraction of the context window
(`skillListingBudgetFraction`, default ~1%), the **least-used** descriptions are dropped from the
listing — a verbose or rarely-hit description can price *itself* out of discovery. One more reason the
description earns its length, and the body carries the depth.

## The durable insight: the triggering ceiling is tool choice, not the description

For a **search/tool-shaped** skill (one that wraps something a built-in tool or an MCP server already
does), the limiter on whether it fires is the **agent's tool choice**, not the wording of the
description. Nothing in the description biases the model toward a skill over a built-in or MCP tool that
already answers the request — so a simple, one-step ask ("find where X is defined") will often go
straight to the built-in tool and skip the skill *even with a perfect description*.

Practical consequence: **invest in the body, ship a validated description, and stop tuning it.** A skill
earns its trigger by covering a *multi-step procedure with judgment* that no single tool call replaces —
not by out-phrasing a tool the model would reach for anyway. Chasing the last few percent of trigger rate
by rewording is low-yield; a sharper procedure and a cleaner negative cut are not.

## Anti-patterns

- A description that names abstract categories instead of the phrases a user actually types.
- No negative cut → two overlapping skills that both fire, or both stay silent.
- Depth crammed into `SKILL.md` instead of `references/` → every session pays for detail it rarely needs.
- A skill that hardcodes one path/ID → not reusable; make it an argument.
- A `grep`-based smoke → fork-bombs the env the moment anyone runs it.
- Over-tuning the description of a tool-shaped skill (see the ceiling above) — spend it on the body.
