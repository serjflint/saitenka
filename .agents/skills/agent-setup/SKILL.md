---
name: agent-setup
description: >-
  Enable this repo's agent tooling (repowise + Basic Memory MCP, pyrefly LSP, repowise hooks) in the
  coding agent you use — Claude Code or Codex. Use when asked to "set up the tooling", "wire up
  repowise / Basic Memory", "enable the MCP servers", "register the LSP", "how do I turn this on in
  Claude / Codex", or after cloning when the agent can't see repowise. Renders the canonical
  .agents/mcp/servers.json into each agent's format via scripts/render.py. NOT for *using* the tools
  (when to reach for repowise vs LSP vs Basic Memory → the `agent-tooling` skill); NOT for the
  underlying install/reproduction of repowise / MLX / Basic Memory (that is the docs site,
  contributing/agent-tooling.md).
metadata:
  project: saitenka
---

# agent-setup

Turn on this repo's optional agent-intelligence stack in your agent of choice. The stack is **optional**
— the repo builds and tests without any of it.

There is **no shared cross-agent MCP config format** (Claude uses `.mcp.json`, Codex uses
`.codex/config.toml` TOML), so the servers are defined once in **`.agents/mcp/servers.json`** and
`scripts/render.py` emits each agent's dialect. Never hand-edit an agent's MCP config — edit
`servers.json` and re-render.

## Enable in your agent of choice

| Artifact | Claude Code | Codex |
|---|---|---|
| **Guidance** (`AGENTS.md`) | bridged via `CLAUDE.md` → `@AGENTS.md` — nothing to do | read natively in-project |
| **Skills** (`.agents/skills/`) | `ln -s ../.agents/skills .claude/skills` (local, git-ignored) | discovered directly — nothing to do |
| **Rules** (`.agents/rules/`) | `ln -s ../.agents/rules .claude/rules` (local, git-ignored) — a no-`paths:` rule auto-loads globally; `/memory` to verify | read directly (path-scoped) |
| **MCP servers** | `uv run .agents/skills/agent-setup/scripts/render.py --agent claude` → writes `./.mcp.json` (git-ignored) → **approve** the project servers on next launch | `uv run …/render.py --agent codex --out ~/.codex/config.toml` → merges a `# BEGIN/END saitenka managed MCP` block (idempotent). ⚠️ Codex may not auto-load repo-local `.codex/config.toml` — prefer `~/.codex/config.toml` |
| **Python LSP** (pyrefly) | the `pyrefly-lsp` skill's `.lsp.json` registers `pyrefly lsp` for the built-in `LSP` tool (needs a Claude restart to bind) | `pyrefly` CLI — allow-list `Bash(pyrefly:*)` |
| **repowise hooks** | `~/.claude/settings.json` `SessionStart`/`PostToolUse` → `repowise-augment` | `~/.codex/hooks.json` `PreToolUse` → `repowise-rewrite --agent codex` |

## Prerequisites (per user, not committed)

- `uv tool install repowise pyrefly basic-memory` (and an MLX/OpenAI-compatible endpoint for repowise +
  Basic Memory retrieval — see the docs site).
- Basic Memory: register your own vault as a project in `~/…/basic-memory config.json`; `servers.json`
  carries only the offline guardrails, never a vault path.

## Re-render after changing servers.json

`servers.json` is the source of truth. After editing it, re-run `render.py` for each agent — the Claude
`.mcp.json` is overwritten and the Codex block is replaced in place. `scripts/smoke.sh` asserts the
render round-trips.
