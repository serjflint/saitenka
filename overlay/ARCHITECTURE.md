# Architecture

## What this is

`saitenka-overlay` renders Yomitan `structured-content` (styled CJK text with ruby/furigana) as an
image, composited directly into mpv's own OSD surface via `overlay-add` — one surface, no second
window, so it survives fullscreen and sidesteps the Windows airspace/MPO bugs a second window would
hit. Beyond the renderer, the codebase bolts a full reader onto mpv: subtitle draw with per-word
hitboxes, hover → dictionary lookup → tooltip, word coloring by known/frequency/JLPT state, and
one-key Anki mining.

Design strategy: "simplest tool first, escalate on limits" (see README's Escalation ladder) —
Pillow does the rendering today; Rust + cosmic-text + the libmpv render API is the fallback only if
Pillow hits a real wall (per-frame animation, huge panels, GPU scaling).

## Module map

- **`sc/`** — Yomitan `structured-content` model and parsing (the input format). Kept PIL-agnostic
  (enforced by `.importlinter`).
- **`render/`** — layout/flow: the text walker, ruby positioning, line wrapping, panel chrome.
  `render.flow` is the core.
- **`draw/`** — rasterization primitives that paint the laid-out content.
- **`raster/`** + top-level **`panel`** — compose the final RGBA panel image; `Definition`/`Entry`
  (in `panel.py`) hold one dictionary's rendered entry for a word.
- **`mpvio/`** — the mpv IPC bridge: JSON-IPC transport (`ipc.py`), mpv/ffmpeg discovery
  (`discover.py`), pushing panels into mpv's OSD surface (`osd.py`).
- **`app/`** — the application layer. `controller.py`'s `Reader` is the main-loop orchestrator
  (poll mpv → tokenize → hover hit-test → lookup → mine); `tokenize.py` (fugashi/unidic-lite word
  segmentation); `dictionary.py`/`dictdb.py`/`lookup.py` (the consolidated SQLite dictionary DB);
  `scoring.py`/`wordlists.py`/`fsrs.py` (word coloring); `anki.py`/`miner.py` (mining); `jimaku.py`
  (subtitle fetching); `cli.py`/`cli_run.py` (the entry point — thin parser + real orchestration).

## Data flow (the hover → lookup → render → mine chain)

1. `Reader` polls mpv's `sub-text`/`mouse-pos` over IPC (event-driven `observe_property`).
2. Each subtitle line is tokenized (`tokenize()`, fugashi + unidic-lite) into `Token`s with
   per-word hitboxes, drawn as an OSD overlay.
3. On hover, hit-testing maps screen coordinates to a token; the word's lemma is looked up against
   the consolidated dictionary DB, producing an `Entry` (one `Definition` per configured
   dictionary).
4. The panel code (`panel.py`) walks the `Definition`s' structured content into a rendered tooltip
   image via `render/` → `draw/`, composited over the mpv frame.
5. Optionally, mining the hovered word builds an Anki note via AnkiConnect (`anki.py`, `miner.py`):
   sentence, screenshot, audio clip, provenance.

## Load-bearing decisions

- **Single-surface compositing** (`overlay-add`, not a second window) is the whole point — it's
  what makes this airspace-safe on Windows fullscreen.
- **Dictionaries are imported once** into a consolidated SQLite DB (the Yomitan model); play-time
  only opens it — nothing rebuilds during playback, RAM stays low.
- **GPL-3.0 `saitenka_deinflect` is chokepointed**: only `app/dictionary.py` and `app/doctor.py`
  may import it (enforced by import-linter + ruff `TID251` + the license gate) — keeps the core
  Apache-2.0-clean.
- **Free-threaded runtime** (Python ≥3.13, adopts 3.14t where available) — `assert`s across the
  codebase double as GIL-off guardrails.

## Test doubles (for the mpv boundary)

- **`FakeIPC`** (`tests/util.py`) — in-process double for the mpv IPC client; feeds
  subtitle/mouse properties and property-change events so `Reader`'s full loop runs without a real
  mpv.
- **`Driver`** (`tests/driver.py`) — wraps a `Reader` + `FakeIPC`, drives it through the *real*
  input path (mouse moves, clicks, keys) so tests read as interaction scripts while still
  exercising genuine hit-testing.
- **`FakeMpvServer`** (`tests/fake_mpv_server.py`) — a real unix-socket server double, one layer
  lower than `FakeIPC`, for attach-mode/transport tests needing actual socket/connection behavior.

---

Dependencies: `pyproject.toml`. Setup/run/test steps: README.md / RUNNING.md. Task-by-task dev
gate: the `dev-gate` skill.
