---
name: pyrefly-lsp
description: Do not invoke. Infrastructure-only — the sibling `.lsp.json` registers the pyrefly Python language server (`pyrefly lsp`) with Claude Code's built-in LSP tool, giving go-to-definition, find-references, call hierarchy, and document symbols for .py/.pyi files. There is nothing to run here.
metadata:
  project: saitenka
---

# pyrefly-lsp

Infrastructure-only plugin. Its purpose is the sibling `.lsp.json`, which registers `pyrefly lsp` as the
Python LSP server for Claude Code's `LSP` tool. Portable command (`pyrefly` on PATH) so a contributor
who runs `uv tool install pyrefly` gets symbol navigation on clone.

- **Server binary:** `pyrefly` on PATH (`uv tool install pyrefly`; upgrade with `uv tool upgrade
  pyrefly`).
- **Nothing to invoke.** Claude should never trigger this as a skill.
- **Nav backend only.** pyrefly here backs the interactive `LSP` tool; **basedpyright** remains a
  blocking checker in the `poe types` gate (see the `dev-gate` skill). Different jobs.
- The `LSP` tool binds its server at **session start**, so a Claude restart is required after enabling.

## Trusting the answers

The reference index is built **once per server** and only ever catches up through file watchers that
Claude Code does not enable — so it silently drifts from disk as a session runs. Verified in
`pyrefly/lib/lsp/non_wasm/server.rs` (v1.2.0): `indexed_configs` / `indexed_workspaces` are
insert-only sets, and `setup_file_watcher_if_necessary` registers nothing unless the client advertises
`didChangeWatchedFiles.dynamicRegistration` (silent `_ => ()` otherwise).

It answers a stale query with a **plausible wrong result, never an error** — most often just the
definition itself. That reads exactly like "this symbol has no callers", which is how it produces a
confidently wrong refactor.

- **`hover` / `documentSymbol` are always trustworthy** — they read the open document, not the index.
- **`findReferences` / `incomingCalls` / rename need the index.** A lonely result is a *failure
  signal*, not a finding: re-query, then corroborate with `git grep` before acting on it.
- **The first query of a session can outrun the background index.** One measured session returned 1
  reference, then 34 for the same symbol moments later. Never conclude from the first call.
- **Restart the session after disk moves under the server** — branch switch, rebase, merge, a codemod
  writing new modules. *Not* after a commit: committing changes nothing on disk, so it cannot stale
  the index.
- **Do not set `disable-project-excludes-heuristics`.** It addresses the editable-install defect of
  facebook/pyrefly#2667 / #3553 / #3704, which this repo does not have — a fresh server indexes this
  tree completely with the heuristics active — and it would pull `.venv` into the indexed set.
