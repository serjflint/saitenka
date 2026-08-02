# Rule: searching the tree

Prefer the harness **Grep/Glob tools** or **`git grep`** — they honor `.gitignore`, so they skip the
untracked env/cache trees entirely.

The real hazard is **fan-out**, not any single command: `find . | xargs grep` from the repo/workspace
root enumerates thousands of files under `.venv*`/caches and spawns a `grep` **per batch**. That once
ran away — and the cleanup made it worse, because reaching for `ps | grep` / `pgrep` to diagnose a
runaway just spawns more of the same. (Search binaries on a given machine may also be PATH-shimmed to a
wrapper, which amplifies the fan-out, but the pipeline is the root cause either way.)

**Rules**

- Never `find … | xargs grep`.
- Scope shell searches to a concrete subdir (e.g. `overlay/src/overlay/app`) — never the repo/workspace
  root; or use `git grep` / `git ls-files` (tracked files only).
- Process work: `pkill` / `killall` / `kill`. Don't diagnose or fix a runaway with `ps | grep` / `pgrep`.
