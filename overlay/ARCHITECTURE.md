# Architecture

## What this is

`saitenka` renders Yomitan `structured-content` (styled CJK text with ruby/furigana) as an
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
  `render.flow` is the core. `render/window.py` is the PIL-free geometry kernel (block offset table +
  half-open visible-range) and `render/banded.py` the **windowed (banded) tooltip engine**
  (`WindowedPanel`): render only the blocks in the viewport±overscan, retain heights/hit-geometry past
  pixel eviction (each retained block zlib-compressed), composite O(viewport) — pixel-identical to a
  `render_panel` crop. It is the **sole tooltip compositor** — every popup (base / nested / kanji /
  search) is a `Panel` (`app/popups.py`) wrapping one `WindowedPanel`.
- **`draw/`** — rasterization primitives that paint the laid-out content.
- **`raster/`** + top-level **`panel`** — compose the final RGBA panel image; `Definition`/`Entry`
  (in `panel.py`) hold one dictionary's rendered entry for a word. Value types with no render deps —
  `model.Theme`, `version.overlay_version` — live at the package root to keep `render`/`app` acyclic.
- **`parallel.py`** — the CPU-bound-render executor policy: free-threaded threads (FreeType releases
  the GIL, faces are thread-local; ~78% of the render tail is `getmask2`/`getlength`), process-pool
  fallback on a GIL build. Sub-interpreters are out (PIL's C extension segfaults across them).
- **`mpvio/`** — the mpv IPC bridge: JSON-IPC transport (`ipc.py`), mpv/ffmpeg discovery
  (`discover.py`), pushing panels into mpv's OSD surface (`osd.py`).
- **`app/`** — the application layer. `controller.py`'s `Reader` is the main-loop orchestrator
  (poll mpv → tokenize → hover hit-test → lookup → mine); `tokenize.py` (fugashi/unidic-lite word
  segmentation); `dictionary.py`/`dictdb.py`/`lookup.py` (the consolidated SQLite dictionary DB);
  `scoring.py`/`wordlists.py`/`fsrs.py` (word coloring); `anki.py`/`miner.py` (mining);
  `episode_analysis.py`/`analysis_overlay.py` (cached whole-track metrics and their background UI);
  `session_stats.py` (event aggregation and asynchronous local history, reusing analysis snapshots);
  `jimaku.py`/`tsukihime.py`/`subtitle_providers.py`
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

## Tooltip render pipeline (end-to-end)

The 5-step chain above is the *reader loop*; this section zooms into how one tooltip actually gets on
screen — from the speculative work done *before* a hover, through the DB query, layout, band raster,
compression, and blit. It is the canonical walkthrough; the module docstrings own the fine print.

### The entities (what each noun means)

| Entity | Lives in | What it is |
| --- | --- | --- |
| **Token** (the *term*) | `app/tokenize.py` | One segmented word: `surface`/`lemma`/`reading`/`pos` + its subtitle hitbox. The lemma is the DB lookup key. |
| **Dictionary** | `app/dictionary.py` | One imported Yomitan dict over the consolidated SQLite DB. Holds a per-instance LRU of decoded `DictEntry` (`entry_cache_max`). |
| **`Entry`** | `panel.py` | The whole tooltip's content for one term: a ruby headword + one **`Definition`** per configured dictionary (+ freq pills, pitch graphs, inflection chain). ≥2 readings ⇒ one **`EntryGroup`** per reading. |
| **Panel** | `app/popups.py` | The cached, view-bearing tooltip: a `Panel` wraps exactly one `WindowedPanel`. Base / nested / kanji / search popups are all `Panel`s. |
| **`Row`** | `panel.py` | One horizontal slice of the panel (header, freq row, a def-name chip, or a **def body**), as a *deferred thunk* — building rows walks no content. Only def-body rows are expensive. |
| **Block** | `render/document.py` | One block-level unit inside a def body (a paragraph or list item) after the SC-walk. A pathological def body is ~79 `<div>`s flattened into a tall stack of blocks. |
| **Band** | `render/banded.py` | A ≤`_BAND_PX` (256px) horizontal slice of a **row** — the unit of raster, cache, and eviction. A row of height `H` has `ceil(H/256)` bands. |
| **Layout** | `render/flow.py`, `render/document.py`, `body_block.py` | The wrapped-but-not-drawn state: `FlowLayout` (one flow → line boxes), `DocLayout` (stacked blocks + tops), `LaidOutBody` (a def body's walk+wrap, memoised per row). Cheap; no `getmask2`. |
| **Offset tables** | `render/window.py` | `OffsetTable` (exact block starts/ends + the half-open visible-range kernel) and `LazyOffsets` (heights filled in as rows measure — exact for the visited prefix, estimated below). |
| **`ScanBox` / `LinkBox`** | `model.py` | Per-CJK-char hover hitboxes and per-`<a>` click regions, in panel space — retained past pixel eviction so a scrolled-away word still hovers. |
| **`CachedBlock` / `BlockGeom`** | `render/banded.py` | A cached band's pixels (`zlib`-packed) at its `(row, band)` key; `BlockGeom` is the row's retained hit geometry, never evicted. |

### Containment (what's inside what)

The entities nest three ways — too deep to draw as one matryoshka, so read them as three
"what's inside what" views. The **runtime panel** (the object that lives in the cache) and the
**layout inside one def-body Row** (the deep dolls, rebuilt from a memoised handle) are the two
render-side hierarchies; the **content model** is the input that `panel_rows()` turns into the rows.

*Runtime panel — the outer shells (one per shown word, held in `panel_cache`):*

```
┌─ Panel  (app/popups.py — the cached tooltip) ─────────────────────┐
│ reading + one WindowedPanel                                       │
│ ┌─ WindowedPanel  (render/banded.py — banded compositor) ────────┐│
│ │ rows: list[Row]          ← built once by panel_rows(Entry)     ││
│ │ _offsets: LazyOffsets    ← per-row heights + gaps → scroll math││
│ │ ┌─ _blocks: {(row, band) -> CachedBlock}  ← LRU pixel cache ──┐││
│ │ │ one band's image, zlib-packed; + row-local scan/links       │││
│ │ │ bounded O(viewport) — even a 34x-viewport row keeps ~2-3    │││
│ │ └─────────────────────────────────────────────────────────────┘││
│ │ ┌─ _geom: {row -> BlockGeom}  ← retained hit geometry ──┐      ││
│ │ │ ScanBox/LinkBox in panel space; never evicted, so a   │      ││
│ │ │ scrolled-away word still hovers                       │      ││
│ │ └───────────────────────────────────────────────────────┘      ││
│ └────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────────┘
```

*Inside ONE def-body Row — the deep layout dolls (behind `measure`/`render_window`, all off one
memoised `LaidOutBody`; the row is rasterised across `ceil(height / 256px)` bands → one `CachedBlock`
each):*

```
┌─ Row  (def-body; panel.py)  — one memoised layout handle ─────────┐
│ measure() · render_window(y0,y1) · geometry() · render()          │
│ ┌─ LaidOutBody  (body_block.py)  — walk()+wrap once, cached ─────┐│
│ │ ┌─ DocLayout  (render/document.py)  — stacked blocks + tops ──┐││
│ │ │ ┌─ LaidBlock[]  — one per block (paragraph / list-item) ──┐ │││
│ │ │ │ ┌─ FlowLayout  (render/flow.py)  — the wrapped flow ──┐ │ │││
│ │ │ │ │ ┌─ line[]  — one wrapped visual line ──┐            │ │ │││
│ │ │ │ │ │ Item[]:  text | ruby | img | chip    │            │ │ │││
│ │ │ │ │ └──────────────────────────────────────┘            │ │ │││
│ │ │ │ └─────────────────────────────────────────────────────┘ │ │││
│ │ │ └─────────────────────────────────────────────────────────┘ │││
│ │ └─────────────────────────────────────────────────────────────┘││
│ └────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────────┘
```

*Content model — the INPUT (`panel_rows(Entry)` flattens it into the `Row[]` above), and where its
glossary comes from:*

```
Entry  (one hovered term's whole tooltip content)
├─ headword(ruby) · reading · tags · freqs[] · pitches[] · inflection_chain[]
├─ defs: Definition[]      ← FUSED layout: one per configured Dictionary
│    └─ Definition { dict_name · content = SC node · tags[] }
└─ groups: EntryGroup[]    ← only ≥2 readings: one block/reading, its own ⊕
     └─ EntryGroup { headword · reading · defs: Definition[] }

  Definition.content is decoded from:
  dictionaries.sqlite  (one consolidated DB, opened read-only at play time)
  └─ Dictionary[]  scoped by dict_id
       └─ DictEntry { term · reading · glossary = SC nodes · tags }  (LRU 256/dict)
```

### Stage 1 — speculative prefetch (before the hover) · `app/prefetch.py`

Every subtitle-line change enqueues background work on the persistent prefetch worker pool
(`_AUTO_WORKERS_FREE_THREADED = 4` free-threaded, `_AUTO_WORKERS_GIL = 2`, or a pinned
`prefetch_workers`). A generation counter (`_prefetch_gen`) invalidates in-flight jobs the instant the
line changes. Three job classes, cheapest-first:

- **Warm** (`PrefetchItem(full=False)`) — decode + cache each dictionary's glossary JSON into the
  `Dictionary._entry_cache`. The JSON decode is the single biggest per-word cost in a `--stress`
  profile, so paying it during idle playback makes the eventual hover a cache hit. Runs for **every**
  new line, and — with `prefetch_lookahead > 0` (default `0`) — for the next N cues too (needs an
  external sub index).
- **Head render** (`HeadPrefetchItem`) — for a *selective* subset of the next `head_prefetch_lookahead`
  cues' words (default `1`), speculatively render the same viewport-capped head a hover would, straight
  into the shared `panel_cache`. Selectivity is the cap: only n+1 / forgotten / rare-frequency words
  qualify (`_head_priority`), known/mined words never. Bounded by `head_prefetch_queue_max = 24`
  in-flight (the transient-RSS cap, distinct from `panel_cache`'s retained-size LRU).
- **Render-ahead** (`RenderAheadReq`) — the on-screen tooltip's scroll warm; see Stage 6.

`mined` state (jamdict/`card_for`) is resolved on the **main thread** and passed by value — jamdict is
not worker-safe under free-threading.

### Stage 2 — lookup → `Entry` · `app/dictionary.py`

On hover, the token's lemma (plus surface + reading forms) is looked up. `DictionarySet._batch_exact`
issues **one** `IN`-list SQL query across all configured dicts (`_EXACT_Q`, `ORDER BY e.id` for
deterministic reassembly), then each `Dictionary` decodes its rows into `DictEntry` (LRU-cached,
`entry_cache_max = 256`/dict). The assembled `Entry` carries one `Definition` per dict. This is the
only synchronous DB touch on the hover path; prefetch usually paid it already.

### Stage 3 — build rows (deferred) · `panel.py`

`panel_rows(entry, width)` produces the `Row` list — header, pitch/inflection/tag/freq/reading rows,
then per-dict `(def-name chip, def-body)` pairs. **Nothing is walked or drawn here.** Each def-body row
closes over one memoised `layout_body_block` handle and exposes `measure()`, `render_window(y0,y1)`,
`geometry()`, plus the full `render()` (the golden/finish source of truth). `Panel.from_rows` wraps
them in a `WindowedPanel(compress=True)`. Reference width is `384`px at `scale 1.0`; the runtime
`tip_width` scales with window height (mpv's OSD model — same content at any size).

### Stage 4 — measure (layout-only, no raster) · `render/banded.py` → `body_block.py`

To show at scroll `S`, `WindowedPanel` grows its exact-offset prefix to cover `S + view_h + overscan`
by **measuring** each row top-down:

- **body rows** call `Row.measure()` → `layout_body_block` runs `walk()` + wrap → a `LaidOutBody` whose
  `full_height` seeds `LazyOffsets` — **without any `getmask2`**. The `walk` (~200ms on pathological
  entries — the *other* big cost besides drawing) runs **once per row** (memoised) and is run ahead of
  the viewport, so a band never pays it synchronously.
- **non-body rows** (header/chip/freq — one band, never split) render eagerly to learn their height.

Hit geometry (`ScanBox`/`LinkBox`) is seeded from the *layout* at this point (`document_geometry`), so
a measured-but-not-yet-rastered row is already hoverable (a scroll-jump to the bottom keeps the top
resolvable). `LazyOffsets` stays exact for the visited prefix and estimated below it (`seed_height =
200`), so incremental scroll never mis-lands a notch.

### Stage 5 — raster a band · `render/flow.py`, `render/document.py`

A visible band `[y0,y1)` of a body row is drawn by `raster_body_window` →
`render_document(y_window=…)` → `render_flow_window`: only the wrapped lines overlapping the window get
a `getmask2`, into a `(y1−y0)`-tall image. It is **pixel-identical to the full render cropped to that
band** (a band is a shorter block at a within-row offset) — the whole property the composite rests on
(`test_body_window.py`, `test_document_window.py`). At ≈**0.034ms/px**, a 256px band ≈ **9ms**, under
the 16ms frame budget even on a cold miss; a whole 34×-viewport block was ≈**500ms**.

### Stage 6 — cache, compress, composite, evict · `render/banded.py`

- **Cache**: each rendered band becomes a `CachedBlock` keyed `(row, band)` in an LRU, its RGBA
  bytes `zlib.compress(…, 1)`-packed (`compress=True`) — a mostly-transparent panel packs hard, so a
  warmed cache keeps the old whole-panel blob's memory profile.
- **Composite**: `viewport(scroll, view_h, overscan)` builds an ephemeral per-band `OffsetTable` and
  hands it to `composite_window`, which clips each visible band to the viewport by an integer crop and
  `alpha_composite`s it — O(viewport), byte-identical to a one-shot `render_panel` crop.
- **Evict**: `_evict` drops bands **per band** outside `[scroll−overscan, scroll+view_h+overscan]`, so
  even one row 34× the viewport retains only the ~2–3 overlapping bands (a currently-visible band is
  never evicted; `max_cached_blocks` caps the LRU when set).
- **Render-ahead**: on a wheel notch (`_scroll_tip`, step `round(osd_h·0.08)` ≈ **86px** at 1080p),
  `Panel.render_ahead(overscan=view_h)` warms the next screen's bands off the main thread — threads
  calling `render_window` on a free-threaded build, a process pool (`render_body_band`, injected to keep
  the `render → body_block` import contract) on a GIL build. At flick (~2600px/s) the one-screen lead is
  ≈**0.16s**, in which a ~9ms band is warmable ~18× over — so the worker keeps ahead where it couldn't
  on a ~500ms whole block.

### Stage 7 — blit to mpv · `mpvio/osd.py`, `app/popups.py`

`Panel.viewport` converts the composited RGBA to a premultiplied BGRA array (`to_bgra_array`) and
pushes it into mpv's own OSD surface via `overlay-add` — one surface, no second window (the load-bearing
decision below). Scrolling re-runs Stage 6 for the new offset; a warm frame is a compress→decompress +
composite + convert, no `getmask2`.

### Stage 8 — mine (optional) · `app/anki.py`, `app/miner.py`

One key builds an Anki note over AnkiConnect: sentence, screenshot, audio clip, and provenance. The
hovered `EntryGroup.card_index` selects exactly which entry is mined.

### Constants, limits, and measured timings

Values are **defaults** unless noted; each lives at the cited symbol (the SSOT — this table points, it
doesn't own). Timings are order-of-magnitude, measured on the pathological corpus under free-threaded
3.14t (`examples/bench_responsiveness.py`).

| Knob / metric | Value | Where |
| --- | --- | --- |
| Band raster/cache unit | `_BAND_PX = 256`px | `render/banded.py` |
| Estimated height before measure | `seed_height = 200`px | `WindowedPanel` |
| Scroll overscan (warm margin) | one screen (`overscan = view_h`) | tooltip blit + `Panel.render_ahead` |
| Wheel step | `round(osd_h·0.08)` ≈ 86px @1080p | `Reader._scroll_tip` |
| Base tooltip viewport cap | `tip_max_frac = 0.4` of video height | `PerfOptions` |
| Reference panel width / scale | `384`px @ `scale 1.0` (margin 16, gap 7, body-indent 20) | `panel.py`, `model.Theme` |
| Prefetch workers | 4 (free-threaded) / 2 (GIL) / pinned | `app/prefetch.py` |
| Decode-warm lookahead | `prefetch_lookahead = 0` cues | `PerfOptions` |
| Head-render lookahead / queue | `head_prefetch_lookahead = 1`, `head_prefetch_queue_max = 24` | `PerfOptions` |
| Decoded-entry LRU / panel LRU | `entry_cache_max = 256`/dict, `panel_cache_max = 128` | `DbOptions`, `PerfOptions` |
| Raster cost | ≈0.034ms/px → 256px band ≈9ms | bench |
| SC-walk cost (pathological) | ≈200ms/row, run once + ahead | bench |
| Whole tall block (pre-band) | up to 14 700px ≈500ms `getmask2` | bench |
| Scroll frame p50 (banded) | ≈10ms flick / 14ms normal (< 16ms budget) | bench |
| Worst first-reach (banded) | ≈12ms (≈1 band; was ≈530ms) | bench |

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

Dependencies: `pyproject.toml`. Setup/run/test steps: the [Development](https://saitenka.readthedocs.io/en/latest/contributing/development/)
docs + `poe` tasks. Task-by-task dev gate: the `dev-gate` skill.
