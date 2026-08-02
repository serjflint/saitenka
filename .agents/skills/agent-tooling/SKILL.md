---
name: agent-tooling
description: >-
  Pick the right code-intelligence tool for a task in this repo — repowise (MCP), the pyrefly LSP tool,
  or Basic Memory (MCP). Use when deciding how to answer "who calls / where is / find the symbol",
  "what's risky to change", "what did we decide and why", "what's the architecture", or "what do my
  notes say" — instead of blind grep-and-read. NOT for turning the tools ON in an agent (install /
  register MCP / LSP → the `agent-setup` skill); NOT for reproducing the underlying stack (docs site,
  contributing/agent-tooling.md).
metadata:
  project: saitenka
---

# agent-tooling

Three code-intelligence tools back this repo. Reach for them before grep-and-read — they are exact and
far cheaper. All optional; the repo works without them.

## Which tool

- **pyrefly `LSP` tool** — exact symbol graph. `findReferences` / `incomingCalls` / `outgoingCalls` /
  `documentSymbol` / `goToDefinition`. The canonical first move before touching a symbol (map callers,
  not a maybe-incomplete grep). See AGENTS.md § Refactoring. (Backend: `pyrefly lsp`; basedpyright stays
  the type-gate, not the nav.)
- **repowise (MCP)** — whole-repo understanding from the built index. `get_answer` (how/where/why,
  content-grounded), `search_codebase` (semantic/symbol/path), `get_context` (a file's map), `get_risk`
  before editing a hotspot, `get_why` for decision rationale, `get_overview` once in an unfamiliar area.
  Prefer these over re-reading files. Freshness: `repowise update` after big changes; it never edits
  CLAUDE.md/AGENTS.md.
- **Basic Memory (MCP)** — the maintainer's Markdown knowledge base (Obsidian vault): design notes,
  decisions, tooling procedures (e.g. `notes/tooling/repowise-local-indexing`). `search_notes` /
  `read_note` / `recent_activity`. Personal + per-user (each dev brings their own vault); complements the
  file-based auto-memory. `write_note` is mutating.

## Quick routing

| Question | Tool |
|---|---|
| "Who calls `build_note`?" / "where is X defined?" | `LSP` (findReferences / goToDefinition) |
| "What's the riskiest file to touch here?" | repowise `get_risk` / `get_overview` |
| "Why is the tokenizer matching shaped this way?" | repowise `get_why` |
| "What did we decide about dict tabs / the port?" | repowise `get_why` or Basic Memory `search_notes` |
| "How do I set up / reproduce this stack?" | the `agent-setup` skill · docs `contributing/agent-tooling.md` |

## Enabling these

Not wired yet? → the **`agent-setup`** skill (renders MCP config, registers the LSP, symlinks skills).
