# Agent tooling (optional local intelligence stack)

Saitenka is built with coding agents (Claude Code, Codex). To make them fast and grounded on *this*
codebase — instead of re-reading files and grepping — the repo wires an **optional** local
intelligence stack. None of it is required: the repo builds, tests, and ships without any of it, and it
is entirely local-first (no code leaves your machine).

Three tools, one shared backend:

| Tool | What it gives an agent | Transport | Backend |
|---|---|---|---|
| **[repowise](https://repowise.dev)** | whole-repo Q&A, architecture map, change risk, decisions, dead code | MCP (stdio) | local MLX/Qwen + a built index (`.repowise/`) |
| **pyrefly** | exact symbol graph: references, call hierarchy, document symbols | Claude `LSP` tool / CLI | — |
| **Basic Memory** | the maintainer's Markdown notes (design, decisions, procedures) | MCP (stdio) | local MLX/Qwen embeddings |

*Using* these (which tool answers what) is the `agent-tooling` skill; *enabling* them in your agent is
the `agent-setup` skill. This page is the **install / reproduction** reference.

## Local inference (MLX LM + Qwen)

repowise and Basic Memory both need an OpenAI-compatible endpoint for generation + embeddings. On Apple
Silicon that is one [`mlx-openai-server`](https://pypi.org/project/mlx-openai-server/) serving two Qwen
models on demand.

```bash
uv tool install mlx-openai-server
mlx-openai-server launch --config mlx-server.yaml
```

`mlx-server.yaml` — one endpoint, both models, unload-swap so only one is resident at a time:

```yaml
server:
  host: 127.0.0.1
  port: 11435          # off the crowded :8080 (llama.cpp/Tomcat/dev servers default there)
models:
  - model_path: mlx-community/Qwen3.5-9B-MLX-4bit   # generation (repowise pages)
    model_type: lm
    on_demand: true
    on_demand_idle_timeout: 60
    context_length: 65536
  - model_path: /path/to/qwen3-embedding-4b-4bit-dwq  # retrieval, 2560-dim
    model_type: embeddings
    served_model_name: qwen3-embedding-4b
    on_demand: true
    on_demand_idle_timeout: 60
```

- **One online bootstrap, then offline runtime.** The chat model is pulled from Hugging Face on first
  run; the embedding weights are a locally-quantized MLX directory. After the first fetch, export
  `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` so the runtime never phones home — the same guardrail
  repowise and Basic Memory rely on.
- **Lifecycle.** A small `ensure-mlx-server` wrapper (health-check `/v1/models` → `nohup` launch under a
  lock → wait) makes startup lazy; both consumers point their base URL at `http://127.0.0.1:11435/v1`
  with a placeholder key. One endpoint — never separate generation/embedding servers.

### Why these two models

The retrieval model was picked from a small candidate sweep (a fixed set of code-doc queries; scores are
**directional**, not a leaderboard — the eval is 12 queries over 47 docs):

| Candidate | dim | recall@1 | recall@3 | recall@5 | MRR | query (ms) | disk |
|---|---|---|---|---|---|---|---|
| fastembed (bge-small) | 384 | 0.58 | 0.75 | 0.83 | 0.69 | 4 | 0.1 GB |
| mlx (bge-large) | 1024 | 0.83 | 1.00 | 1.00 | 0.90 | 19 | 1.2 GB |
| qwen3-embedding-0.6b | 1024 | 0.83 | 0.92 | 1.00 | 0.88 | 145 | 0.4 GB |
| **qwen3-embedding-4b** ✅ | **2560** | **0.83** | **0.92** | **0.92** | **0.88** | **67** | **2.3 GB** |

No candidate dominates at this eval size. **qwen3-embedding-4b (2560-dim, vector-only)** was chosen for
its stronger general/multilingual grounding on real repo prose and its behaviour under the hybrid +
reranker experiments (the reranker was dropped as not worth the cost), accepting a middling
latency/size. Generation uses **Qwen3.5-9B-MLX-4bit**, which beat Qwen3-8B on a controlled
`dictionary.py` page-quality benchmark. Readings and pitch always come from dictionaries, never a model.

## repowise (codebase intelligence)

repowise indexes the repo into `.repowise/` (git-ignored) and serves it over MCP. This repo runs a
locally-patched build (opt-in page-scoped repository evidence in synthesis prompts; upstream proposals
`repowise-dev/repowise#1227` + `#1229`) — upstream works too, with lower onboarding-page quality.

```bash
uv tool install repowise            # or --from <local checkout> for the patched build
repowise init --yes                 # builds the index; no API key needed
repowise update                     # resync after big changes
repowise doctor                     # db / FTS / vector store / coordinator in sync
```

`.repowise/config.yaml` pins `provider: openai`, `model: mlx-community/Qwen3.5-9B-MLX-4bit`, `embedder:
openai` / `qwen3-embedding-4b`, and `editor_files: {claude_md: false, agents_md: false}` (repowise never
overwrites our hand-owned AGENTS.md or a local CLAUDE.md shim). `.repowise/.env` sets `OPENAI_BASE_URL=http://127.0.0.1:11435/v1`,
`OPENAI_API_KEY=not-needed`, `REPOWISE_EMBEDDING_MODEL=qwen3-embedding-4b`, `REPOWISE_EMBEDDING_DIMS=2560`.
Keep exactly one index at the repo root. Maintainer depth (manual page corrections, the operating
sequence) lives in the vault note `notes/tooling/repowise-local-indexing`.

## pyrefly (Python LSP)

The agent's symbol navigation is backed by **pyrefly** (Rust, fast) via Claude Code's built-in `LSP`
tool:

```bash
uv tool install pyrefly
```

The `pyrefly-lsp` skill's `.lsp.json` registers `pyrefly lsp` for `.py`/`.pyi`; Claude binds it at
session start (restart after enabling). Codex uses the `pyrefly` CLI directly.

!!! note "pyrefly vs basedpyright — two jobs"
    pyrefly is the **agent nav backend**. basedpyright is a **type-checker in the `poe types` gate** —
    which checkers that gate runs is defined in `[tool.poe.tasks]` (the source of truth), separate from
    nav. Different tools, different jobs — see the `dev-gate` skill.

## Basic Memory (personal knowledge base)

[Basic Memory](https://github.com/basicmachines-co/basic-memory) serves the maintainer's Obsidian vault
(design notes, decisions, tooling procedures) over MCP. It is stock upstream and **personal** — bring
your own vault or skip it.

```bash
uv tool install basic-memory
basic-memory project add <name> /path/to/your/vault
```

Its "local-only, never-internet" behaviour is just environment variables (`BASIC_MEMORY_FORCE_LOCAL=true`,
`HF_HUB_OFFLINE=1`, …) — carried in `.agents/mcp/servers.json`'s `env` block, so no wrapper script is
needed. Semantic search points at the same MLX endpoint (`:11435`, 2560-dim). `write_note` is mutating
(approval-gated on Codex).

## Wiring it into your agent

There is **no shared cross-agent MCP config format**, so the servers are defined once in
`.agents/mcp/servers.json` and rendered per agent. Don't hand-edit an agent's config — edit
`servers.json` and re-render. See the **`agent-setup`** skill for the full activation matrix (MCP, LSP,
skills symlink, hooks); in short:

```bash
# Claude Code — writes ./.mcp.json (git-ignored), approve on next launch
uv run .agents/skills/agent-setup/scripts/render.py --agent claude

# Codex — merge a managed block into ~/.codex/config.toml
uv run .agents/skills/agent-setup/scripts/render.py --agent codex --out ~/.codex/config.toml
```
