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
