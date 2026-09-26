# Diagnostic coverage

A span count is not a coverage score. Coverage needs a declared scenario, its applicable
configuration, a production trigger, and an assertion against the collected artifact.
The initial inventory is `tests/fixtures/telemetry-scenarios.json`; it is deliberately not
a claim that every listed platform, renderer, failure, or integration has been exercised.

## Reading a report

`saitenka report` creates a ZIP with a versioned `diagnostics/envelope.json`, `versions.txt`, a
redacted `doctor.json`, and the latest session's own `overlay.log` lines and trace. The file log writes
the video and subtitle files Saitenka resolves, and the titles parsed from them, as `<media:…>` /
`<title:…>` digests — also inside provider statuses and exception text — and cue text, looked-up words
and dictionary queries as `<text:… len=N>`; home paths become `<HOME>` / `<USER>`. Cue timings and
content digests remain, and so do configuration names such as deck, note type and dictionary. A
title of one short word is not replaced, so the word stays readable elsewhere in the log. Trace
attributes get the same replacement when exported, and the report then drops any attribute that
still names a media or subtitle file. `doctor.json`'s `recent-errors` keeps only its count; its
excerpts come from every logged session and need `--diagnostic-detail`. A
session whose lines predate that format (`log_format` in each record) ships neither its log nor its
trace; `logs/collection.json` says `predates-sanitised-format`.
The envelope separates the collector build from the producer recorded in the latest log session's summary.
Missing, malformed, mismatched-session and unsupported-schema summaries cannot establish healthy
operation counts. Matching identity fields do not prove identical loaded code: editable source can
change while a process is running. The config subset is attributed to the collector's file, not the
player's effective configuration.

Runtime diagnostics require `telemetry.enabled = true`. When disabled, reports mark runtime
evidence as `not-collected`; they cannot establish successful zero counts. When enabled, producers
enqueue individual records through OpenTelemetry. Its existing writer owns the bounded histories
and report summary; cue processing never copies retained history. Queue loss, malformed diagnostic
records and sampling mark operation health as partial. The OTel SDK kill switch disables collection.
Playback identity and cached subtitle publication do not depend on recording.

Native geometry contributes a separate producer-side configuration history: frame/storage dimensions,
aspect, margins, renderer parameters, feature flags and text-free font-setup metadata. Foreground and
prefetch render spans identify their owner and configuration revision; accepted publication has its
own span, including cache-served geometry. These are the request values consumed by the geometry
boundary, not independently observed mpv values. Accepted results additionally carry the libass
version used by that geometry backend and the mask source (`native-original`, `request-document`,
or unknown for older results). `subtitle_geometry_native_reference` separates reference-render and
attribution cost, retained mask bytes and bounded attribution-failure reasons. These measure worker
work, not display presentation. Other loaded native libraries, resolved font faces,
font-content identity and other player settings remain unknown. The text-free document summary
records source kind, declared script/layout resolution, style/event counts and allowlisted tag
presence; missing declarations stay absent rather than becoming inferred defaults.

`session_configuration` records selected session-construction options with their actual CLI,
configuration-file, default or programmatic origin. It does not claim live renderer readback.
Its profile history separates requested language/tokenizer from the committed values, including
rejected switches and incomplete post-commit work. Profile names and arbitrary option strings are
not shared. Both histories are bounded to four owners; process-local construction evidence is not
a reconstruction of every later override.

Accepted geometry also retains aggregate same-renderer verdicts and mask-eviction counts. These
survive warm-cache publication while recording is enabled; stale generations cannot become current
findings. They describe redraw eligibility and retained coverage, not upload or physical pixels.
The bounded `renderer_selection` history distinguishes an explicit legacy-mode request from the
post-draw ownership state. Repeated unchanged draws do not consume history; missing older records
remain unknown. Ownership is not a screenshot or an independent fidelity verdict.

The history retains four configurations per owner and four owners, with eviction counts. A revision
identifies changes in the recorded subset or configured font paths, not every possible renderer input.
`requested` and `published` describe the current generation; `last_published` is historical and can
survive invalidation/close. Evicted references are marked rather than silently joined to newer values.
Retained spans may reference owners or revisions no longer present in the bounded report.
No publication record certifies displayed pixels. Collection adds no IPC queries, pixel probes or
font hashing; the OpenTelemetry writer persists the bounded snapshot.

`player_configuration` separately records the option values read when native geometry evaluates a
configuration, including configurations it refuses. Numeric, boolean and enumerated values are
allowlisted; font names/paths, style overrides, crop expressions and color strings are redacted.
Unavailable means the existing property reader returned `None`, not a diagnosed query error. Invalid
values are distinct from zero/false. CLI/profile origins and applied state remain unknown.

The player-option history also retains four revisions per owner and four owners. Revisions identify
changes in the shared subset or source class; changes between redacted values are not distinguished.
`player_configuration_read` spans identify new revisions, and geometry-decision spans reference the
last read revision. Those references are historical: an early refusal can precede a fresh read, and
closed owners retain history. Values may come from the observation cache or a query; they are not an
atomic mpv snapshot, backend readback, or a join to a particular published geometry request. Retained
traces may outlive the corresponding history. Collection introduces no additional player queries.

`player_query_health` records the gateway's startup and reconnect attempts to subscribe to and read
rendering properties. Observer-registration replies are separate from query replies. Timeout,
disconnect, missing property, unavailable property, malformed reply, exception and successful null
or missing-data replies remain distinct. Reply values and freeform errors are never retained.

Each of two retained gateway owners keeps its latest attempt per property/verb in the latest
recorded connection epoch. A newer epoch replaces prior rows; superseded or post-close completions
cannot overwrite them. `player_property_command` spans join by owner/sequence/epoch. Absent rows are
not recorded, not successful. A pending row on a closed owner is historical unfinished evidence,
not a claim that a command still runs. The minimal no-gateway adapter and later direct queries
remain unmeasured. Successful subscription
does not prove delivery or application; applied settings and pixels remain unknown.

Each owner's `ingress` records stage-decision counts and its eight most recent rendering-property
decisions across connection epochs, with explicit eviction. `wire` notifications are distinct from
synthetic `replay-read` events. Closed, stale-epoch, not-ready, reconnect-buffered, candidate-full and
mailbox-full outcomes cannot establish admission. A buffered event may later be queued or discarded;
these are stage counts, not unique-event counts, and buffering has no terminal correlation yet.
`queued` means mailbox admission only. Schema-2 ingress adds reducer outcomes from
`projection-mailbox` and `projection-direct`; `reduced` means the reducer ran, not that an effect
was applied or pixels were displayed. Schema-1 reports retain their admission evidence with unknown
projection counts. `player_property_ingress` spans join the history by
`query_owner`/`ingress_sequence`; queued rows also carry the mailbox sequence and connection epoch.
The owner's epoch describes command history; use each ingress row's epoch for ingress evidence.
Rows are ordered by recording sequence, not necessarily wire arrival order under concurrency. No
property values or arbitrary names enter this evidence. Missing legacy ingress remains unknown,
whereas present zero counts mean no recorded decisions of that kind for that owner.

Explicit `--attach` files are a separate unredacted tier: at most four attachments, 8 MiB each.
The CLI lists them before collection; the ZIP inventories generic member names and sizes in
`attachments/manifest.json`. Sources are never modified or deleted. No attachment is collected by
default, and user-owned exports never expire. No new automatic sensitive-witness store is enabled.
Report readers do not extract archives and reject unsafe paths, duplicate/ambiguous members and
oversized compressed or expanded input.
Trace readers distinguish missing, readable-empty, partially malformed and invalid captures. Valid
rows in a partial capture remain usable, with the rejected-row census retained; an unsupported trace
schema cannot establish current evidence.
The diagnostic analyzer also reads collection rejections and exporter loss counters. A truncated
source rejected before ZIP inclusion remains incomplete capture evidence, not an empty healthy trace.

The producer-summary reader accepts at most 128 KiB; an oversized summary yields
`unreadable-or-too-large`, not healthy or zero activity. Combined-history tests guard this budget.

The trace-based tools below read the default report's trace. `saitenka report --diagnostic-detail`
adds redacted raw configuration, mpv config and log, frame diagnostics, crash reports and every
logged session, which can still contain private paths and text. `--no-log` excludes logs and frame
diagnostics; it does not sanitize the remaining detail.
Review the ZIP's manifest and contents before sharing. Nothing uploads automatically and exported
ZIPs do not expire automatically. Publication fails rather than overwriting a timestamp collision;
retry with a different timestamp or destination after preserving the earlier report.

### Subtitle frame timing

With telemetry enabled, `saitenka run` records IPC enqueue, transport-write and reply-receive
boundaries keyed by connection, epoch and request ID. OSD payload hashes link those requests to
the temporary diagnostic mpv build's command and composition events; subtitle text is not included
in those identities. Subtitle-clock observations include `sub-delay`, including negative offsets.

The diagnostic build reads `SAITENKA_MPV_TRACE`, supplied automatically by the launcher, and flushes
its bounded frame trace on normal exit. Quit mpv before collecting the report. Detailed reports
include `diagnostics/mpv-frame.json` (binary hash, capabilities, initial clock and trace health)
and, when available, `diagnostics/mpv-frame.tsv`. Stock builds produce no frame trace; absent,
partial and overflowing traces do not qualify as complete evidence. `--no-log` omits both members.
GPU submission establishes which composition contains color, not physical display presentation.
The mpv peer binding applies to the initial connection epoch; a reconnect is explicitly marked
unmapped, so the old binding cannot qualify later frame correlations.

From a development checkout:

```sh
uv run python tools/trace_report.py report.zip
uv run python tools/report_color_latency.py report.zip
uv run python tools/telemetry_coverage.py report.zip --require tooltip-quality
```

The coverage command reports required evidence present/absent and optional identity joins.
Joined scenarios also count started, incomplete, orphan, unidentified and schema-invalid events.
`--require` gates presence; `--require-complete` gates the declared joined chain. Neither can infer
events lost before collection, and neither certifies pixels. Identity types remain distinct.
Each scenario declares its missing-evidence category, uncertainty and next required evidence. The
inventory's evidence-loss controls check the reader schema; they do not count as runtime fault
injections. Explicit fault contracts separately check production-to-report diagnoses.
Repeat `--character-corpus DIRECTORY` to account separate pinned corpora; add
`--require-character-qualification` to reject absent, malformed, incomplete or failing results.
This validates supplied manifests, controls and exact numerical verdicts; it does not rerun mpv.
`--fault-case ID` evaluates a declared injected-fault contract from the same inventory: expected
categories, severity, retained evidence and required uncertainty/next evidence. A report matching
that contract does not itself prove the fault was injected. Missing evidence cannot satisfy the
stale-result control. The reported fault denominator is separate from the larger scenario inventory;
families without a declared executed fault contract remain unqualified.
The detail tier includes the same metadata envelope as the default report, selected from the same
log session as its trace. Allowlisted per-operation health counts identify failing boundaries without
sharing detailed traces; recording must be enabled. Pending work is not proof of a crash and a failed
boundary is not its root cause.
It also diagnoses recorded player query/admission failures from the envelope alone. Retained
mailbox-to-reducer joins have their own denominator; missing terminals can mean in-flight work or
lost evidence. Neither those joins nor a successful query qualify pixels or benchmark cost.
It does not certify fault detection from the presence of an event. A failed required scenario
returns nonzero; an empty requirement list stays unobserved. Test references locate evidence,
not executed configuration cells. Preserve untested cells rather than extrapolating from macOS.

Use separate serial runs with and without `--trace-output NEW_TRACE` to compare the production span
path with its exporter enabled. Output records exporter drops and bytes; worker thread-CPU does not
include the writer thread's CPU. Compare identical case/geometry/cycle rows, not only maxima.

Device eligibility reasons are separate from `subtitle_device_upload` terminal outcomes: an accepted
upload is not proof of physical presentation. A failed upload permits retry of identical pixels.
The integration benchmark retains per-occurrence phase counter deltas and bounded generation/reason
records. Its strict cue census is unchanged; an extra redraw is not silently deduplicated.

## Local replay and reduction

`uv run python tools/replay_render_configuration.py REPORT --output NEW_JSON` selects a retained current geometry
publication and emits a recipe. Use `--owner` when several owners are retained; `--historical` explicitly
selects the last publication. Missing or stale configuration is refused. `--execute` runs the original
synthetic ASS corpus with the captured geometry and renderer settings, using the pinned licensed font.
Replay confirms the configuration is executable but does not qualify pixels. Original subtitle
text, resolved fonts, mpv composition and display color processing are not reconstructed or qualified.

`uv run python tools/minimize_subtitle_repro.py SOURCE --output NEW_DIRECTORY --checker COMMAND {input}` reduces a
local ASS/SRT reproducer. The checker must return 0 for a passing candidate, 1 only for the same defect,
and another code for inconclusive evidence. Commands run without a shell, with a 30-second per-check
timeout and a bounded check count (`--max-checks`). Serialization must preserve the initial failure;
every retained reduction is checked. Text reduction keeps combining marks and ZWJ sequences together
but is not a full Unicode grapheme segmenter; it skips ASS-tagged
events; event reduction remains available. This is greedy reduction, not a globally minimal result.
Outputs may still contain copyrighted/private text: nothing uploads, source files stay untouched,
and the new output directory is a user-owned export with no automatic expiry.

Measure these independently:

| Measure | Denominator | Passing evidence |
|---|---|---|
| Diagnosability | Declared applicable scenarios | Correct diagnosis from a real bundle |
| Outcome completeness | Admitted operations | One terminal result or explicitly pending |
| Fault detection | Injected fault cases | Correct incident; healthy control stays healthy |
| Artifact completeness | Required sources | Available, correctly scoped, untruncated evidence |
| Configuration coverage | Scenario × platform/backend/tracing cells | Actually executed cell |
| Causal continuity | Declared async boundaries | Correct parent/link or explicit identity join |

Explaining why a source is missing accounts for it; it does not count as successful collection.
Telemetry disabled, unavailable SDK, dropped samples, absent fields, and measured zero differ.

## Evidence semantics

- Complete-span `dur` measures its named scope. Deferred operation spans measure wall-clock
  lifetime, not a busy thread. `cpu_ms` is separate. `surface_write.round_trip_ms` measures
  submission to terminal reply; the completion callback's duration is not that wait.
  Surface records retain acceptance separately from the reply: a successful but stale acknowledgment
  rejected after removal or shutdown cannot establish current pixels.
- Cue revisions distinguish repeated text. The acknowledgment readout retains unknown eligibility
  and missing acknowledgments, and never inserts a draw estimate into acknowledgment percentiles.
  Its current strict readout covers the native overprint slot, not every color device.
- `tip_compose.soft_reason` classifies compositions: empty means crisp, missing means unknown.
  `saitenka.crisp.compositions` replaces the misleading swaps counter. Per-view quality events
  distinguish submission, acknowledgment, failed/superseded work, and abandoned upgrades.
  Installed JSON output includes ordered per-view lifecycle records. No event proves physical
  display presentation.
- Explicit parent IDs describe causation. A child starting after its parent ended is legal;
  recurring work attached to startup is separately flagged as a suspected context leak.
  Legacy containment-based self-time is only an estimate. Summaries use the full exported
  population before display-record retention.
- CTF schema 2 preserves bounded status, exception classifications and links, but deliberately
  omits exception messages/stacks. The health sidecar reports failed writes, recreated history,
  sampling failures and pending spans independently of the trace. A missing clean-end marker is
  unknown, not proof of a crash.
- Reports select a trace matching the log session when known. Collection records explain rejected,
  unavailable and truncated sources. Native fault logs are historical unless session attribution
  is known. Rotating log snapshots do not establish complete session intervals.
- A bounded text-free deferred-operation summary survives without the tracing SDK. It is saved
  during session-history writes and shutdown, under the cache's `diagnostics/` directory, retaining
  ten sessions. Without history writes, abrupt termination may precede the first summary. It is
  not an inventory of all product operations. Traces remain opt-in; nothing uploads automatically.

Bridge tests in `tests/test_trace_continuity.py`, `tests/session/test_surface_trace_bridge.py`,
`tests/test_integration_telemetry.py` and `tests/test_crashlog.py` exercise production work through
export/collection. Analyzer controls also cover missing fields, broken trees, repeated cues and
biased retained tails. These complement rather than replace the existing runtime fault tests.

The scenario inventory's `bridge_cases` names exact production-to-reader tests and their endpoints.
Run every parameterized case and retain the pytest result; `telemetry-coverage` lists these contracts
but never turns their presence into execution evidence. These differ from the narrower `--fault-case`
report-matching denominator and from independent character qualification. Shutdown's bridge covers an
accepted tooltip interaction; it does not certify every broker lane's retirement.

## Workloads and calibration

The responsiveness timeline accepts `--osd 3440 1440 --tooltip-scale 1.5` and reports attempted,
opened and settled interactions. `--require-scenario nested` fails when the requested path did
not open and settle. Compare serial tracing-off/on repetitions with identical inputs and
`--telemetry-dir`; preserve both sample populations. See [continuous benchmarks](continuous-benchmarks.md)
and `BENCHMARKS.md` for existing budgets and profiler selection. Missing player frame counters
are unavailable measurements, not zero dropped frames.

The timeline starts the session lifecycle and feeds synthetic cue/pointer observations through a fake
player. Nested work uses visible scan-cell coordinates; a submitted request alone is not a settlement.
It does not measure real player event delivery or display pixels.
Trace replay substitutes vocabulary and infers actions from output cadence; it is not exact
incident replay. Passing an existing speed gate does not certify quality settling or telemetry overhead.

Calibration runs after visible-write submission and compares union bounds. Missing replies can
retry within the bounded signature budget; late viewport/source generations cannot establish
agreement. This is neither a screenshot probe nor a per-kanji oracle. No heavy production pixel
sampling is enabled by the character tool below.

## Local character comparison

### Corpus domains

The original corpus's required pixel domain is locked in
`tests/fixtures/character_corpus/cases.json`: twelve cases generated separately as ASS and SRT.
The targeted comparison matrix is both formats × 1280×720/3440×1440. Styles and fractional placement
are ASS-specific; SRT conversion is a separate arm, not equivalent styled coverage. Phase/origin
sweeps are separate same-renderer invariants. This is targeted pairwise coverage, not the unrestricted
product of fonts, effects, displays and text.

The `Character corpus` CI workflow runs these four cells under Xvfb with pinned mpv and the
bundled licensed font. It uploads the generated inputs, exact manifests, results and bounded
failure images even when qualification fails. Private media is never an input to this workflow.
The candidate uses the production whole-cue raster preparation and composition path; this corpus
does not qualify the whole-cue OSD or automatic device-selection modes. Its versioned
`opaque-coloring-v2` oracle requires every ink component, stable opaque interiors, placement, the
requested color, and an actual published frame. Exact source-alpha differences remain recorded as
diagnostics: libass can change faint edge antialiasing when color boundaries split bitmap runs even
though the opaque overpaint contract remains satisfied. The oracle's bounded edge and component
error limits are explicit qualification policy and are guarded by displaced, missing-stroke,
absent-output, and wrong-color controls; they are not a general libass error bound.
A single faint boundary pixel is intentionally indistinguishable from antialias phase variation;
detached marks and longer faint strokes remain required. Excess ink or component counts make the
coordinate inconclusive before component analysis allocates unbounded state.
The capture profile forces rendering when the diagnostic window is obscured; an unresponsive
player fails startup rather than supplying unknown font settings. On macOS the profile uses the
system render timer, avoiding dependence on a paused diagnostic window's display-link callback.

For a local public run, generate inputs with
`uv run --extra full tools/synthetic_characters.py --output /absolute/new-corpus`, then run
`uv run --extra full tools/compare_cached_characters.py --cached-subtitles /absolute/new-corpus/original.ass --synthetic --size 1280 720 --output /absolute/new-ass-1280 --execute`.
Repeat for `original.srt` and for `--size 3440 1440`, using a new output directory for each of the
four cells. Do not add `--limit` or `--only-kanji` when qualifying the full census.

Broader domains reuse existing corpora rather than silently enlarging the pixel-qualified claim:

| Domain | Existing discriminator | Qualification scope |
|---|---|---|
| Simultaneous events, automatic wrapping, font substitution, styles | `tests/fixtures/libass_token_matrix.json` | Locked native capability/support contract; not strict mpv pixels |
| Clipping and inverse clipping | `test_clipped_native_ink_refuses_an_unclipped_overprint` in `tests/test_fractional_overprint.py` | Changed native coverage retained; unclipped redraw refused |
| Missing native runtime | `test_missing_native_runtime_is_not_a_successful_mask_fallback` in the same file | Dependency failure, never oracle success |
| Animation, bidi, ligature boundaries, drawing runs | Existing token-matrix fallback candidates | Explicit capability results; no animation/cluster ownership certification |
| Detached/combining marks, interior dakuten, repeated text, fractional sizes/scales | Original character corpus and fractional tests | Same-renderer invariants plus separately executed mpv qualification |

No domain changes from failed to unsupported merely to clear a run. Non-ink coordinates remain in
the overall character census. Corpus generation and local minimization require no episode or network;
the minimizer's regression preserves an injected native-mask-authority defect after reduction.

### Invariant controls

These controls have distinct scopes; a fast matcher test cannot qualify the independent capture path.

| Invariant | Positive control and deliberate fault | Environment |
|---|---|---|
| Fractional placement survives serialization | Native phase match versus integer-position reconstruction in `tests/test_fractional_overprint.py` | Same-host libass and bundled font |
| Marks and glyph phase belong to the matched mask | Matching mask versus removed/extra mark and same-sized wrong phase in that file | Pure matcher |
| Original pixels remain authoritative | Unmodified native reference versus token-color run splitting; production upload versus one-pixel displacement in `tests/test_native_pixel_assembly.py` | Native library, no player |
| Cached/stale work cannot certify a new occurrence | Warm/cold agreement versus generation, variant and superseded-result rejection in `tests/test_subtitle_pipeline.py` | Worker/fake boundary |
| Accepted reply is distinct from arrival | Current reply versus post-remove/shutdown late reply in `tests/session/test_surface_trace_bridge.py` | Real runtime, deferred fake player |
| Coverage needs a complete declared chain | Complete schema versus lost terminal, missing identity and rejected acknowledgment in `tool_tests/test_telemetry_coverage.py` | Offline report reader |
| Independent composite detects incorrect output | Correct composite versus displaced and wrong-color captures in the character runner | Explicit real-mpv execution |

```sh
uv run --extra full python tools/compare_cached_characters.py \
  --cached-subtitles /absolute/cache/episode-raw.ass \
  --video /absolute/media/episode.mkv --output /absolute/new-results
```

Without `--execute`, this freezes the full coordinate census and reports every coordinate as
unattempted. In a desktop session, add `--execute --limit 8 --only-kanji` for a bounded pilot;
omit selection flags for the full corpus. ASS and SRT variants require separate output directories.
The caller explicitly selects the cached variant: track metadata absent from a cache remains unknown.
No subtitle is downloaded or re-extracted. Inputs remain read-only; font attachments are copied
to a local black reference container. All source text, fonts and images stay in local output.

The manifest binds subtitle/video hashes, implementation identity, rendering profile, geometry,
coordinate key-set and mpv build. Private runs inventory macOS ambient font files; other ambient
providers remain unqualified. Public synthetic runs instead pin the bundled font and disable system
font providers, including on Linux. Exact matching manifests allow resume; already classified rows
are retained. Delete
nothing to retry a changed profile: use a new output directory. Results checkpoint after each
coordinate; interruption retains the original denominator and leaves unfinished work unattempted.

A kanji is a text character. A libass glyph is a shaped font glyph: variation selectors,
combining marks, ligatures and fallback can make character-to-glyph mapping non-bijective.
The tool uses conservative base/mark/variation-selector/ZWJ clusters, not a complete Unicode
grapheme implementation. It recolors a target in the full authored event context to obtain
an independent mpv ownership mask; it never clips the reference to our token boxes.
Cluster-to-token/device attribution is reported separately from glyph identity.

Captures must be repeatable and recoloring must preserve reference intensity. Color-matrix
compatibility is disabled for the diagnostic primary-color masks. Unsupported color/shaping or
animated cases remain inconclusive; a midpoint sample cannot qualify animation. Converted SRT
must first agree with mpv's original SRT rendering. The oracle checks missing ink, intensity
disagreement and extra output; spill permission uses original context support, without a dilated
border allowance. Production overprints add no border beyond the native outline.
Zero numerical tolerance is a diagnostic criterion, not a calibrated cross-platform quality budget.

`results.json` separates passed, failed, unsupported, inconclusive and unattempted coordinates,
including a kanji-only denominator. `index.html` indexes results; at most twenty image triplets
are retained. Nonzero exit means disagreement or incomplete qualification, not necessarily a
confirmed product defect. Synthetic negative controls include an interior shift with unchanged
cue bounds, missing color, translucent intensity errors and distant extra output.

## Independent bounds oracle: issue 468

The [issue](https://github.com/serjflint/saitenka/issues/468) remained open on 2026-09-13.
Whole-cue and hit-map qualification provide runtime evidence, but its acceptance is not automatically
complete:

| Acceptance | Current evidence |
|---|---|
| Like quantities | Union ink bounds and pixel support; not pen advances |
| Plain ASS and converted SRT bounds test | Partial: no complete paired bounds matrix |
| Perturbed style negative control | Existing live layout-mismatch control; character controls are additional, not equivalent |
| Explicit unsupported layouts | Bounds probes cannot qualify collision-dependent layouts; character captures decline unqualified shaping/animation |

Neither this issue nor a passing character subset certifies whole-product telemetry coverage.
