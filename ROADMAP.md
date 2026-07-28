# Roadmap

The direction and near-term plans for Saitenka. This is the high-level "what & why"; concrete,
trackable work lives in the issue tracker and milestones. Shipped work is in
[CHANGELOG.md](CHANGELOG.md). Contributions toward anything here are welcome — see
[AGENTS.md](AGENTS.md) for how to work in the repo.

## Direction

Saitenka is a **grounded, high-performance in-mpv immersion engine**: readings and pitch always come
from dictionaries (never a model), coloring reflects live FSRS review state, and the overlay stays a
thin surface composited into mpv's own OSD — no second window. Near-term work advances three fronts:
**correctness** of annotation and known-word matching, **latency** of the render/lookup path, and the
**quality gate** (architecture ratchet, benchmarking, CI). Individual items live as issues.

## Explicitly not planned

- **Async / `aiohttp` HTTP stack** — the app is synchronous and its HTTP volume is tiny and mostly
  localhost; going async would be a large refactor with a free-threading (C-extension GIL) risk for
  no meaningful gain.
- **Library-browsing / media-center integrations** (e.g. Jellyfin discovery, an fzf/rofi launcher) —
  mpv plays whatever it's handed; discovery/queueing is a separate tool's job, not this overlay's.
- **WebSocket / texthooker output stream** — Saitenka does its lookups and mining in-mpv; there's no
  browser texthooker to feed.

## How this roadmap is kept

Curated by hand and reviewed against reality, not auto-generated. Near-term items graduate to issues
and milestones; when they ship they move to the changelog. Automated changelog generation (e.g.
[git-cliff](https://git-cliff.org/) with Conventional Commits) may be adopted later, but the
changelog is written for readers, not derived from commit messages.
