# Saitenka compatibility corpus

This directory defines behavior that another Saitenka implementation can run without importing the
Python package. An implementation supplies an executable adapter; the corpus runner supplies stimuli and
judges the returned boundary transcript.

The reference executable is `tests/contracts/default_adapter.py`. It exposes every capability below over
the same JSON Lines transport; a replacement may use any language and process model as long as it
implements the negotiated capability. Run any implementation with:

```console
uv run python tests/contracts/protocol.py --corpus journey -- ./path/to/adapter
```

The runner sends no `expect` or `expect_contains` field. It validates every result envelope and judges the
observations outside the adapter. A replacement can implement one capability at a time; conformance means
the named v0 capabilities it advertises, not unlisted UI behavior. Card-preview controls, kanji popup
navigation, native font rasterization, and live mpv remain native retained contracts in this version.

## Session v0 protocol

The runner and adapter exchange one compact JSON object per line on stdin/stdout. Diagnostics go to stderr.
The runner starts with:

```json
{"type":"hello","protocol":"saitenka.session/v0","requires":["session-v0"]}
```

The adapter answers:

```json
{"type":"hello","protocol":"saitenka.session/v0","capabilities":["session-v0"]}
```

For each scenario the runner sends a `run` request containing `scenario_id`, `given`, and `when`. It never
sends `expect`; an adapter cannot read its own answer key. The adapter answers with the same protocol and
scenario ID, `status: "ok"`, and an `observations` array. Exit status zero means the adapter produced a
protocol response for every request. Behavioral mismatches are decided by the runner. A malformed request,
unsupported required capability, or adapter failure uses a nonzero exit status.

Actions are ordered. `drain_until_quiescent` processes eligible work and must stop within `max_steps`.

Expectations use one of two comparison modes:

- `exact`: the full observation sequence must match;
- `contains_in_order`: listed observations must appear in order; unrelated output may occur between them.

Session v0 observations come only from external transcripts: commands sent to mpv, visible overlay writes
and removals, clipboard writes, and bounded adapter termination. An adapter may inspect its own internals to
drive the implementation, but it must not publish reducer, store, object, thread, or private-field state as
an observation. Symbolic IDs replace process IDs, addresses, timestamps, and implementation names.

Each scenario file conforms to `schema/session-v0.json`. The reference runner also enforces the same closed
request vocabulary before execution. Scenario IDs are stable API keys. The committed
manifest locks scenario IDs, content-derived nested-vector identities, vector count, and the complete
answer-free input payload plus the complete oracle payload, so a re-vendor or cleanup cannot silently
remove or rewrite behavior.
The test runner validates every committed row against the published Draft 2020-12 schemas.

## Config v0 protocol

`saitenka.config/v0` uses the same JSON Lines `hello`/`run` framing with capability `config-v0`. A run
request contains `scenario_id`, `mode`, `config`, optional `flags` or `mine`, and `observe` paths; expected
values stay with the runner. The adapter returns those normalized dotted paths in `observations`. This makes
run/attach configuration parity a product contract without exposing `ReaderOptions` or Python dataclasses.
Profile-policy rows expose resolved identities, scoped configuration, deterministic cycle order, launch and
mining targets, and script font selection as JSON values. Profile-switch rows exercise cycle order, tokenizer
commit, subtitle-language selection, degraded fallback, retokenization, warming, and notification. Config-policy rows exercise persistent TOML,
environment precedence, telemetry, retired settings, subtitle geometry, and scoring validation. Corpus rows
conform to `schema/config-v0.json`; the runner strips answers and validates mode-specific keys.

## CLI v0 protocol

`saitenka.cli/v0` uses capability `cli-v0`. Inventory scenarios return the actual run-help flags and
registered commands; parser scenarios return only named bound arguments. Config scenarios materialize an
isolated TOML file before parsing, so the corpus covers default precedence without reading a user's config.
Profile-command scenarios run the public add/use/remove/list/show surface against an isolated TOML file and
return normalized config and terminal output.
The open-ended all-command help check and the legacy wrapper process remain native tests because their sets
and process boundary cannot be represented by a finite scenario list without weakening them.
Corpus rows conform to `schema/cli-v0.json`; the runner strips answers and validates mode-specific keys.

## Subtitle v0 protocol

`saitenka.subtitle/v0` uses capability `subtitle-v0`. Render-profile scenarios supply normalized mpv
settings and frame geometry, then observe either portable geometry fields or the setting that makes exact
hit geometry unsafe. Renderer-state scenarios expose the libass scaling values that must match mpv. Corpus
rows conform to `schema/subtitle-v0.json`; Python classes, cache keys, and ownership state stay behind the
adapter.

## Word-state v0 protocol

`saitenka.wordstate/v0` uses capability `wordstate-v0`. It evaluates JSON numeric cases for FSRS
retrievability and frequency/rareness laws. The reference-vector expectations stay with the runner, so an
adapter in another language receives inputs but no answers. Rows conform to `schema/wordstate-v0.json`.

## Analysis v0 protocol

`saitenka.analysis/v0` uses capability `analysis-v0`. It evaluates whole-track lexical metrics and cache
identity from JSON cues and vocabulary snapshots. Lifecycle rows drive the assembled analysis command and
observe visible state, availability, background completion, dependency deferral, and playback neutrality.
Worker interleavings and history persistence remain native concurrency and storage contracts. Rows conform
to `schema/analysis-v0.json`.

## Dictionary v0 protocol

`saitenka.dictionary/v0` uses capability `dictionary-v0`. It imports abstract Yomitan rows into an
isolated adapter store and returns normalized entry, reading-group, term-reachability, and mining-card
observations. Cases may select a profile language to exercise missing-entry text and inflection routing.
Archive, media, telemetry, and connection lifetimes stay native boundaries. Rows conform to
`schema/dictionary-v0.json`.

## Tooltip v0 protocol

`saitenka.tooltip/v0` uses capability `tooltip-v0`. Geometry scenarios supply viewport, word-box, panel,
and user-scale inputs and return only visible sizes and coordinates. The contract covers scale preference,
raster buckets, caps, nested-popup height, clamping, and side selection without exposing controller or cache
objects. Hit-testing scenarios map frame coordinates to interactive token indices while respecting cue
origins and noninteractive tokens. Font rasterization, cross-engine hit-map agreement, hover concurrency,
and interaction state stay native tests. Interaction scenarios cover hover presentation, word copy, link
navigation, and pause ownership through the assembled surface router. Rows conform to
`schema/tooltip-v0.json`.

## History v0 protocol

`saitenka.history/v0` uses capability `history-v0`. Backlog scenarios drive captures, media relocation,
explicit relinking, status changes, text export, and privacy-safe summaries in an isolated store. Session
history scenarios drive clocked playback events and return normalized aggregates and summaries. Storage
migrations, async writer scheduling, and assembled bookmark input stay native tests. Rows conform to
`schema/history-v0.json`.

## Picker v0 protocol

`saitenka.picker/v0` uses capability `picker-v0`. Interaction cases supply provider candidates, warnings,
failures and pointer actions, then observe normalized visible rows, panel capture, scroll, selected downloads,
and close state. Provider scheduling and render geometry stay native tests. Rows conform to
`schema/picker-v0.json`.

## Sidebar v0 protocol

`saitenka.sidebar/v0` uses capability `sidebar-v0`. Interaction cases supply cue timelines, active cue and
pointer actions, then observe visible row windows, manual-scroll expiry, seek commands and playback neutrality.
Row analysis, persistence boundaries and composition stay native tests. Rows conform to
`schema/sidebar-v0.json`.

## Journey v0 protocol

`saitenka.journey/v0` composes the flagship path instead of accepting isolated subsystem answers. Its
declared cue, FSRS snapshot, dictionary entry, pointer action, capture results, and mining format must
produce maturity-colored subtitle tokens, the hovered dictionary tooltip, one media capture, and the final
Anki note for that same selected word. This is the portable wiring proof that connects the subtitle,
word-state, dictionary, tooltip, mining, media, and Anki capabilities. Rows conform to
`schema/journey-v0.json`.
