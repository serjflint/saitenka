# Development

Getting a dev clone running, smoke-testing it, and the manual QA pass. Governance and the standing
engineering rules live upstream:

- [`CONTRIBUTING.md`](https://github.com/serjflint/saitenka/blob/main/CONTRIBUTING.md) — what makes a
  contribution *ready to merge*, the workflow, and the readiness checklist.
- [`AGENTS.md`](https://github.com/serjflint/saitenka/blob/main/AGENTS.md) — the standing rules
  (Python-via-`uv`, Anki access, the LLM policy, tokenizer/golden traps, the dev gate, testing
  invariants).

## Get the source running

```bash
git clone https://github.com/serjflint/saitenka.git
cd saitenka/overlay
uv sync                        # core + dev group: pillow, fugashi+unidic-lite, numpy…
uv run --extra full pytest -q  # sanity: prints "X passed" (full pulls jmdict/deinflect/telemetry)
```

!!! note "Python is `uv`-only"
    Never invoke bare `python` / `pip` / `venv` / `pipx`. Use `uv run` / `uvx` / `uv add`. The lockfile
    (`uv.lock`) is committed; standalone scripts declare deps via PEP 723 inline metadata.

## Quick smoke run

No Anki, no external dictionaries — proves subtitles plus hover on a generated clip:

```bash
uv run python examples/mpv_reader.py
```

A 1080p test clip opens with a Japanese line. Move the mouse over a word → a JMdict tooltip appears
above it. Press ++q++ to quit.

## Full run on the test episode

Once your dictionaries are imported (see [Install](../start/install.md)), the video path is the only
required argument — `--dict` / `--freq` / `--pitch` / known-decks all come from the config:

```bash
cd saitenka/overlay
uv run python examples/mpv_reader.py \
  "/path/to/anime.mkv" \
  --color --mine --start 600
```

- The embedded **Japanese** track is auto-selected and re-drawn by the overlay (mpv's own subs hidden).
  English is leased as the secondary only while the ++t++ translation is visible.
- If Japanese is unavailable, English is shown immediately without tokenization or mining; enabled
  providers fetch a Japanese track in the background and announce it without selecting it.
- Dictionaries are already imported into the consolidated DB, so `run` / `attach` open it instantly —
  nothing is rebuilt at play time.
- `--start 600` jumps ~10 min in (past the OP, into dialogue). Press ++space++ to pause on a line with
  words to scan.

Spelling everything out on the CLI overrides the config, e.g. `--dict … --freq … --pitch …
--anki-decks '{"Saitenka::Known":["Expression"]}'`.

## Manual QA checklist

What to verify after a change that could touch rendering, coloring, mining, or the reader UI:

- [ ] **Subtitles** — the JP line is drawn by the overlay, multi-line wraps, and names with baked-in
      furigana (e.g. `龍門光英…`) are clean (reading stripped).
- [ ] **Coloring** — known words are **green**; the single unknown word in a sentence is **mauve**
      (N+1); others take a frequency-band color; JLPT words get an underline; particles stay plain. With
      `[fsrs]`, learning / young / mature-known / forgotten words use distinct colors in both full and
      hover-only annotation modes.
- [ ] **Tooltip** — hovering shows the dictionaries stacked in config order with ruby examples, anchored
      **above the hovered word's line**; re-hovering is instant (cached).
- [ ] **Frequency pills** — a green row (one pill per freq dict) and a purple pitch pill (`ほんめい [0]`).
- [ ] **Grammar tags** — `noun` / `no-adj` / `suru` render as filled gray pills, not empty boxes.
- [ ] **Mine** (++"Ctrl+m"++) — a green `✚ mined …` preview appears top-left with word, reading,
      sentence (mined word bolded), meaning, the actual frame, and `▶ Ns` — and **you hear the clip**.
- [ ] **Dedup** — mining the same word again shows the **existing** card (`✓ in deck`), no duplicate.
- [ ] **Bulk** (++"Shift+m"++) — toast reads `mined N · M dup`; `Saitenka::Mining` gets N new cards.
- [ ] **Translation** (++t++) — the English line appears above the JP subtitle; ++t++ again hides it.
- [ ] **Syncplay** — with hover auto-pause off (++"Alt+p"++), opening tooltips, switching JP/EN,
      retrying providers, bookmarking, and opening analysis/help do not pause or seek the room.
- [ ] **Fallback** — with no Japanese track, English appears immediately; a fetched track is announced
      but stays unselected until ++"Alt+t"++.
- [ ] **Sidebar** (++"\\"++) — active-cue tracking and manual scrolling work; N+1/N+2 badges appear when
      episode analysis finishes; bookmarked cues survive reopening the same filename.
- [ ] **Session aids** — ++"`"++ shows episode analysis without pausing; ++F1++ shows the effective
      configured shortcuts.
- [ ] **Fullscreen** (++f++) — subtitle, tooltip, and preview all stay correctly placed (airspace test).

!!! warning "Mining writes real Anki cards"
    Cards go to `Saitenka::Mining` tagged `saitenka`. Review or remove test cards in Anki → Browse →
    `tag:saitenka`.

## The dev gate

`uv run poe all` is the pre-push gate — run it before pushing.

!!! tip "Point, don't restate"
    The task list is authoritative in `pyproject.toml` (`[tool.poe.tasks]`). The task-by-task
    breakdown, how to read a failure, the advisory `poe hygiene` tier, and the free-threaded /
    3.13-pinned-env traps live in the **`dev-gate` skill** (`.agents/skills/dev-gate/`) — consult it
    rather than duplicating it here. Authoring a test the house way is the **`write-test` skill**
    (`.agents/skills/write-test/`).

Inner loop (not a gate): `uv run poe affected` runs only the tests a change can touch — seconds instead
of the full suite, for the edit→feedback cycle.

## Optional: agent intelligence stack

If you drive development with a coding agent (Claude Code, Codex), the repo can wire up local,
grounded code intelligence — repowise (whole-repo Q&A), pyrefly (symbol navigation), and Basic Memory
(notes) over a local MLX/Qwen backend. All optional; the repo builds and tests without them. Setup and
reproduction: [Agent tooling](agent-tooling.md).

## Optional: session history & telemetry

Both are **local-only and off by default**:

- **Session history** — set `[stats] enabled = true` to persist local immersion sessions independently
  of telemetry; `saitenka stats` lists recent ones.
- **Telemetry** — runtime tracing/metrics via OpenTelemetry behind two independent switches: the
  `telemetry` extra (the OTel SDK) and the `[telemetry] enabled` config flag. Flip the flag with
  `saitenka telemetry enable` (`status` / `disable` alongside). Each session writes a Chrome Trace
  Format file to `~/.cache/saitenka/telemetry/`; `$OTEL_SDK_DISABLED=true` is the standard kill switch.

The overlay also writes a rotating JSON-lines debug log to `~/.cache/saitenka/overlay.log` (DEBUG in the
file, WARNING+ to stderr) — `jq`-able, and read by `doctor`'s "recent errors" and `report`'s bundle.

## Architecture

Module map and data flow: the [Architecture](architecture.md) page. Shipped changes:
the [Changelog](changelog.md).
