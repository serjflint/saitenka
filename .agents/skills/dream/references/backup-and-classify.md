# dream — classification taxonomy, backup manifest & Basic Memory uplift

Deep detail for `SKILL.md` phases 1–3. Load only when running the skill.

## Classification taxonomy (phase 1)

Tag every memory entry with exactly one:

| Tag | Meaning | Action in phase 3 |
|---|---|---|
| `durable-keep` | Verified, useful across sessions, expensive to rediscover | Keep; tighten the frontmatter `description` (it feeds MEMORY.md) |
| `duplicate-of-canonical` | Restates `AGENTS.md` / `.agents/` | Delete the copy; keep at most a 1-line pointer if it aids discovery |
| `promotion-candidate` | Stable, verified, team-useful — belongs in `.agents/` / `AGENTS.md` | **Recommend only**; needs explicit approval, do not move |
| `temporary-task-state` | Completed-task / session scratch | Delete |
| `stale` | Contradicted by the current repo | Delete or correct in place |
| `cheaply-rediscoverable` | A quick file read / `git grep` recovers it instantly | Delete |
| `unverified-hypothesis` | Not yet confirmed | Keep only if labelled uncertain; else drop |
| `sensitive` | Secret / credential / token value | Delete; never back up the value, never print it |
| `unrelated` | Belongs to another project / user-global | Leave untouched; flag for the user |

A fact belongs in **local memory** when it is useful across future local sessions, expensive to
rediscover, verified (or clearly labelled uncertain), and too local/tentative/operational to be permanent
project policy. A fact belongs in **`.agents/`** only when it is stable, verified, team-useful,
appropriate for every relevant agent, and worth committing as a permanent artifact.

**Verification is against git + file reads** — reconstruct the current truth from the repo, not from the
memory note. A note that can't be confirmed against the code is `stale` or `unverified-hypothesis`, not
`durable-keep`.

## Backup (phase 2)

Location: `~/.claude/memory-backups/<project-slug>/<YYYYMMDD-HHMMSS-UTC>/` — outside the memory-loading
path, never inside the repo or `.agents/`. `<project-slug>` is the memory dir's own slug (e.g.
`-Users-...-saitenka`).

For every file that may be changed / renamed / deleted:

1. Copy it into the backup, preserving its relative path under the memory dir.
2. Preserve file permissions where practical.
3. Append a row to `manifest.json` (or `manifest.tsv`) with: backup-run UTC timestamp · project slug ·
   repo root · active memory-dir path · original file path · backup file path · planned operation
   (keep / trim / rename / merge / delete / uplift) · file size (bytes) · checksum (e.g. `shasum -a 256`),
   when available.
4. Verify each backup file exists and is readable.
5. Compare each backup's checksum against its source.

Do not begin editing until every backup verifies. Never overwrite an existing backup dir (the timestamp
makes each run unique). Never write a secret value into the manifest or any report — a `sensitive`-tagged
entry is recorded by filename only, its content dropped, not archived.

### Report back after phase 2

- backup directory · manifest path · number of files backed up · verification result (all readable? all
  checksums matched?).

## Basic Memory uplift (phase 3)

Uplift moves a fact's *depth* into Basic Memory (MCP, project `saitenka`) so the local file can shrink to
a headline + pointer. It is **verify-and-reconcile per note**, never a blind copy — a Basic Memory note
can itself be stale:

- **Verify** the BM note against current code and git before trusting or pointing at it.
- **Create** the missing current-architecture note when the depth has nowhere to live yet.
- **Mark superseded** any BM note the code has moved past (note the replacement), rather than silently
  deleting the record.
- **Update** a note whose specifics have drifted.

Only once the BM note is trustworthy do you trim the local file to a headline + a pointer to it. Because
`MEMORY.md` is regenerated from each file's frontmatter `description:`, tightening those descriptions is
what actually shrinks the always-loaded index.

**Optional structural dedup** of the BM project's *own* folders (merging redundant folders, fixing
misfiled notes) is in scope — but **never touch the learning-vault / notes content itself**; reorganize
structure, don't rewrite knowledge.
