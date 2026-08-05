# Rule: searching the tree

`grep` / `find` / `rg` / `pgrep` / `ag` are **PATH-shimmed to a mock** in this environment; invoking any
of them in Bash fork-bombs — and reaching for `ps | grep` / `pgrep` to diagnose the runaway just spawns
more. So this is a hard line, not a style preference:

- **Never invoke `grep`/`find`/`rg`/`pgrep`/`ag` in Bash** — not even scoped to a subdir, not via
  `xargs`/`sudo`/`time`. A `PreToolUse` hook (`.agents/hooks/block-shell-search.py`, wired in
  `.claude/settings.json`) **denies** these; if you hit that denial, switch tools — don't work around it.
- **Text / filename search →** the harness **Grep / Glob tools** (they honor `.gitignore`, skipping the
  untracked env/cache trees), or **`git grep`** / **`git ls-files`** in Bash (tracked files only).
- **Symbol nav** (where-defined / who-calls / references) **→ the LSP tool** (`findReferences`,
  `documentSymbol`, `incomingCalls`) — exact, and cheaper than grep-and-read.
- **Process work:** `pkill` / `killall` / `kill` by name or PID. Never `ps | grep` / `pgrep`.

See AGENTS.md **"Tooling — route by intent"** for the full intent→tool table (repowise / Basic Memory too).

## If a search escapes the ban (recovery)

If a forbidden search runs anyway (an indirect invocation the hook missed, or a grep-based script), the
shim self-replicates: it renames its argv to `(statham)` and every proc reparents to `launchd`, so there's
no seeder to kill and a leaf `pkill`/`killall -9` loses the race — the count *grows*. **Freeze before
kill.** Spam `SIGSTOP` across all match forms until every proc is frozen (a stopped proc can't fork, so the
chain dies on its own):

```bash
for r in $(seq 1 40); do pkill -STOP -x statham; pkill -STOP -x grep; pkill -STOP -f find-utils-mocks; done
```

Never interleave a KILL pass — it un-gates the freeze and survivors respawn.
