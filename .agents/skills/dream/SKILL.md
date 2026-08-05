---
name: dream
description: >-
  Refactor this project's local, mutable Claude memory (the per-fact files plus the MEMORY.md index under
  the project's ~/.claude memory dir) so it stays a lean, accurate, always-loaded index — and uplift the
  depth into Basic Memory (the deep durable store, MCP project saitenka). Use when asked to "refactor /
  clean up / prune / dedupe / audit memory", "fix a bloated MEMORY.md", "drop stale or completed-task
  notes", "memory hygiene", "uplift to Basic Memory", "почисти память", "отрефактори память", or reconcile
  memory against the canonical AGENTS.md / .agents/. Always inventories first, backs up before any edit
  (memory is NOT version-controlled), verifies every claim against git and file reads, and keeps the
  one-fact-per-file + frontmatter + wikilink shape. NOT for writing a single new memory (the normal memory
  flow) and NOT for editing CLAUDE.md / AGENTS.md / .agents/ — those are canonical, committed, read-only
  here.
metadata:
  project: saitenka
---

# dream — distill & refactor local agent memory

Keep the project's **local, mutable** Claude memory lean and true without losing durable knowledge, and
without ever confusing it with the **permanent, committed** agent artifacts. Depth and the backup spec are
in [`references/backup-and-classify.md`](references/backup-and-classify.md).

## The two layers (never mix them)

- **Permanent / committed — read-only here.** `CLAUDE.md`, `AGENTS.md`, `.agents/rules/`,
  `.agents/skills/`, other `.agents/` artifacts. In saitenka these are in **git**. Inspect them to verify
  claims; do **not** edit them during a refactor.
- **Local / mutable / uncommitted — the edit target.** The project's memory dir under `~/.claude/`,
  holding one-fact-per-file `*.md` (frontmatter `description` + `[[wikilinks]]`) plus a `MEMORY.md` index.

Keep the **one-fact-per-file** shape. Don't bucket facts into topic aggregates — that breaks the
`[[wikilink]]` graph and the enrichment that reads each file's frontmatter. `MEMORY.md` is regenerated
from each file's frontmatter `description:`, so **tightening those descriptions is the lever** for a lean
always-loaded index.

## saitenka's two-tier model (local index ↔ Basic Memory depth)

Local memory is the **lean, always-loaded headline index**. **Basic Memory** (MCP, project `saitenka`)
is the **deep durable store**. "Uplift" = move a fact's depth into Basic Memory, then trim the local file
to a headline + a pointer.

Uplift is **verify-and-reconcile, not blind copy** — rich ≠ actual. Before trusting a Basic Memory note,
check it against current code and git; **create** missing current-architecture notes, **mark superseded**
ones, **update** stale ones. Only after the BM note is trustworthy do you trim the local file to point at
it.

## Run in three phases; stop for approval after phase 1

### Phase 1 — Locate & inventory (read-only; no edits, no backups yet)

1. Resolve the repo root and the **exact** memory dir from the current Claude Code project mapping — do
   not hardcode. Confirm the dir slug encodes *this* project's path; if you can't map it confidently,
   **stop and ask** — never touch another project's memory.
2. Read every `*.md` in the memory dir; trace the `[[wikilink]]` graph and the `MEMORY.md` index.
3. Read `CLAUDE.md`, `AGENTS.md`, and relevant `.agents/` artifacts as read-only references.
4. **Verify each factual claim against the current repo** (git + file reads) — flag anything stale,
   contradicted, or now cheaply rediscoverable. Cross-check depth against Basic Memory.
5. Classify every entry (taxonomy in `references/backup-and-classify.md`): durable-keep,
   duplicate-of-canonical, promotion-candidate, temporary-task-state, stale, cheaply-rediscoverable,
   unverified-hypothesis, sensitive, or unrelated.
6. Present the inventory + a concrete plan (keep / merge / rename / trim / delete / uplift, and the exact
   backup dir). **Wait for explicit approval before phase 2.**

### Phase 2 — Backup (only after approval)

Memory is not version-controlled, so a backup is **mandatory** before the first edit. Copy every file
that may change to a **timestamped dir outside the repo and the memory-loading path**, with a checksum
manifest, and **verify before editing**. Full spec in `references/backup-and-classify.md`.

### Phase 3 — Refactor

- **`MEMORY.md` = compact index.** High-value facts + one-line pointers (`- [Title](file.md) — hook`) +
  clearly-labelled open issues. Not session logs, debugging narratives, or copies of permanent
  instructions.
- **Each fact file = one fact / a headline + a Basic Memory pointer.** Refresh a stale fact in place;
  delete one that's wrong or trivially rediscoverable; relink dangling wikilinks. Where a fact's depth was
  uplifted, leave a headline and point at the BM note.
- **Dedupe against canonical.** When a memory just restates `AGENTS.md` / `.agents/`, drop the copy and
  leave at most a one-line pointer. Never copy a rule or skill into memory.
- **Promotion is a recommendation, not an action.** If a local fact deserves to become a permanent
  `.agents/` / `AGENTS.md` artifact, list it and get explicit approval — do not move it yourself.

## Safety (hard rules)

- Never run git (or any VCS command) against `~/.claude/` — it is outside any repo.
- Never edit `CLAUDE.md`, `AGENTS.md`, or anything under `.agents/` during a refactor.
- Never move temporary, speculative, machine-local, or secret content into `.agents/`.
- Back up before modifying / renaming / deleting any memory file; keep backups out of the repo.

## Verify

`bash scripts/smoke.sh` — asserts the skill's own structure and the repo-side surfaces it names. (dream
operates on `~/.claude` memory, outside the repo, so only the repo-side references are smoke-checkable.)
