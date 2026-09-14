# Diagnostic coverage

A span count is not a coverage score. Coverage needs a declared scenario, its applicable
configuration, a production trigger, and an assertion against the collected artifact.
The initial inventory is `tests/fixtures/telemetry-scenarios.json`; it is deliberately not
a claim that every listed platform, renderer, failure, or integration has been exercised.

## Reading a report

`saitenka report` creates a metadata-only ZIP with a versioned `diagnostics/envelope.json`.
It separates the collector build from the producer recorded in the latest log session's summary.
Missing, malformed, mismatched-session and unsupported-schema summaries cannot establish healthy
operation counts. Matching identity fields do not prove identical loaded code: editable source can
change while a process is running. The config subset is attributed to the collector's file, not the
player's effective configuration. Runtime configuration, loaded native libraries and pixel fidelity
remain unknown in this envelope.

For the trace-based tools below, collect with `saitenka report --diagnostic-detail`.
That opt-in includes redacted raw configuration, logs, traces and crash reports, which can still contain
private paths and text. `--no-log` excludes logs only; it does not sanitize the other detail.
Review the ZIP's manifest and contents before sharing. Nothing uploads automatically and exported
ZIPs do not expire automatically. Publication fails rather than overwriting a timestamp collision;
retry with a different timestamp or destination after preserving the earlier report.

From a development checkout:

```sh
uv run python tools/trace_report.py report.zip
uv run python tools/report_color_latency.py report.zip
uv run python tools/telemetry_coverage.py report.zip --require tooltip-quality
```

The coverage command reports required evidence present/absent and optional identity joins.
It does not certify fault detection from the presence of an event. A failed required scenario
returns nonzero; an empty requirement list stays unobserved. Test references locate evidence,
not executed configuration cells. Preserve untested cells rather than extrapolating from macOS.

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

## Workloads and calibration

The responsiveness timeline accepts `--osd 3440 1440 --tooltip-scale 1.5` and reports attempted,
opened and settled interactions. `--require-scenario nested` fails when the requested path did
not open and settle. Compare serial tracing-off/on repetitions with identical inputs and
`--telemetry-dir`; preserve both sample populations. See [continuous benchmarks](continuous-benchmarks.md)
and `BENCHMARKS.md` for existing budgets and profiler selection. Missing player frame counters
are unavailable measurements, not zero dropped frames.

Timeline/direct cue setup is a synthetic workload, not an observation-pipeline integration test.
Trace replay substitutes vocabulary and infers actions from output cadence; it is not exact
incident replay. Passing an existing speed gate does not certify quality settling or telemetry overhead.

Calibration runs after visible-write submission and compares union bounds. Missing replies can
retry within the bounded signature budget; late viewport/source generations cannot establish
agreement. This is neither a screenshot probe nor a per-kanji oracle. No heavy production pixel
sampling is enabled by the character tool below.

## Local character comparison

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
coordinate key-set, mpv build and macOS ambient font-file inventory. Other providers remain
unqualified. Exact matching manifests allow resume; already classified rows are retained. Delete
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
disagreement and extra output, allowing the production one-pixel border only for spill support.
Zero numerical tolerance is a diagnostic criterion, not a calibrated cross-platform quality budget.

`results.json` separates passed, failed, unsupported, inconclusive and unattempted coordinates,
including a kanji-only denominator. `index.html` indexes results; at most twenty image triplets
are retained. Nonzero exit means disagreement or incomplete qualification, not necessarily a
confirmed product defect. Synthetic negative controls include an interior shift with unchanged
cue bounds, missing color, translucent intensity errors and distant extra output.

## Independent bounds oracle: issue 468

The [issue](https://github.com/serjflint/saitenka/issues/468) remained open on 2026-09-13.
Runtime union-bounds calibration and `tests/test_live_subtitle_pixel_differential.py` already
provide independent mpv evidence, so its original “all tests share the reproduction” premise
is no longer exhaustive. Its acceptance is not automatically complete:

| Acceptance | Current evidence |
|---|---|
| Like quantities | Union ink bounds and pixel support; not pen advances |
| Plain ASS and converted SRT bounds test | Partial: live ASS pixels and calibration; no complete paired bounds matrix |
| Perturbed style negative control | Existing live layout-mismatch control; character controls are additional, not equivalent |
| Explicit unsupported layouts | Bounds probes cannot qualify collision-dependent layouts; character captures decline unqualified shaping/animation |

Neither this issue nor a passing character subset certifies whole-product telemetry coverage.
