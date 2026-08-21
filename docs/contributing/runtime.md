# Interactive runtime

Saitenka has two runtime packages with different jobs:

- `saitenka.app.runtime` supplies the command table the production `Reader` decides with;
- `saitenka.runtime` holds the runtime itself — events, effects, mailbox admission, effect
  lifecycle, timers, the owner slices and the session loop. It is production, not a contract package:
  `session_routes.py` composes it and a session with a gateway is driven from it.

This page describes both as they exist on `main`. The whole-system module map remains in
[Architecture](architecture.md); native subtitle ownership and its supported operating envelope are
documented in [Native mpv subtitles](../usage/native-subtitles.md).

## Production session

`Reader` owns the live session, interaction, and presentation state. `Reader.run()` hands the thread to
`SessionLoop`, which blocks on the mailbox rather than waking at a cadence:

```text
mpv JSON IPC ──► background reader
                       ├─ events ──► mailbox ──► SessionLoop.receive()
                       └─ replies ─► correlated futures ──► EffectCorrelator

SessionLoop.receive(timeout bounded by the earliest armed timer)
          │  one envelope at a time, in mailbox sequence
          ├──────────────────────┐
          ▼                      ▼
     SessionReactor.handle    Reader's turn, unless the reactor claimed it
     (owner slices, effects)   ├─ reconcile subtitles
                               ├─ apply results
worker result queues ─────────►└─ update interaction

Reader and other callers ──► bounded command queue ──► sole writer ──► mpv JSON IPC
```

The IPC reader performs blocking transport reads away from the session thread. It appends events to
the event buffer and resolves reply futures directly. Replies are matched by request ID; the
connection epoch prevents an old transport from completing work for a replacement connection. A
single writer thread owns physical transport writes. Its queue is bounded, so admission can fail
explicitly instead of growing with input. `close` flushes that queue, bounded, before dropping the
transport — a correlated fire-and-forget carries no implication that its bytes left, so a teardown
write would otherwise be queued and then discarded.

`pump()` does not read the socket. It reports connection loss, performs bounded reconnect work, and
takes one turn off the loop. A conflicting subtitle observation retires the active cue immediately,
before a later cue-dependent command in the same batch can use its tokens or hit boxes.

`CommandRouter` is a closed table of script-message handlers. Duplicate names are rejected, and the
router distinguishes commands that require a current cue from session-wide commands. It is assembled
by `Reader`, so it exposes a composition seam without creating another state owner.

## Background work and publication

Annotation, geometry, tooltip, dictionary, provider, and analysis subsystems can offload work from
the session thread. Each owns its queueing, cache, and synchronous-degradation policy. Background
actors publish result values; the session thread validates them before publishing live interaction
state.

```text
current observation
       │
       ▼
semantic identity ──► worker request ──► result queue
       │                                      │
       └──────────────────────────────────────┤ validate identity
                                              ▼
                                      publish or discard
```

The identity contains the inputs relevant to that result, such as source, track, subtitle role, cue,
profile, rendering space, or interaction job. Arrival order is never sufficient. A source switch,
cue retirement, profile change, reconnect, or close makes incompatible late results inert.

Native subtitle geometry follows the same rule but has a deliberately narrower responsibility:

```text
authored ASS ──► mpv renders visible subtitle pixels
      │
      └────────► libass geometry worker ──► token hit boxes
                                             │
                                             └─► hover, scan, tooltip
```

Geometry augments interaction; it does not choose which subtitle pixels are visible. The ownership
policy and renderer/executor control native visibility and the legacy subtitle surface as a
transaction, including temporary surface suspension. A geometry miss therefore removes or delays
hit boxes while keeping the selected pixel owner stable.

## Playback projection

`PlaybackProjection` (`saitenka/runtime/playback.py`) is the sole interpreter of raw mpv property
observations. `Reader` hands it one ordered observation at a time and applies the typed deltas it
publishes; nothing downstream compares raw property values or decides what a property means.

```text
property-change ──► PlaybackProjection.observe(state, name, data)
                             │
                             ├─ CueIdentityRetired      (conflicting observation)
                             ├─ AuthoredCueStale
                             ├─ SubtitleSelectionChanged
                             ├─ SubtitleTimingChanged
                             ├─ RenderSpaceChanged / GeometryInputChanged
                             └─ SourceChanged / ConnectionChanged
```

The projection owns the immutable facts (connection, media/source, track/role, cue, render space,
timing, pointer, pause), the explicit `Revision` values that make source, track, and render-space
identity comparable without runtime state, and the decision that a given observation conflicts with
the installed cue identity. A conflict retires that identity in the same observation, before a later
cue-dependent command in the same drain can use it.

Every fact the projection sees is published. `LEGACY_OWNED` is the withhold-list that made that
migration incremental — `POINTER` left it when hover moved off the interaction tick, `PAUSE` when
watch time started accruing on the transition — and it is now empty, which is the end state rather
than an oversight (`runtime/playback.py`).

## The runtime contracts

`saitenka.runtime` drives the production session: `app/session_routes.py` installs the reactor and
registers the feature reducers it dispatches to (`poe arch-map` prints the live owner → feature →
event table, which is the count rather than a figure kept here). What the package still *is* —
definable and testable independently of `Reader`, mpv, Pillow, libass, SQLite and Anki — is the
event/effect lifecycle itself:

```text
EventEnvelope
      │
      ▼
SessionMailbox ──► SessionReactor ──► reducer(state, event)
      ▲                                      │
      │                                      ├─ SubmitJob
      │                                      ├─ ScheduleTimer
      │                                      ├─ EmitDiagnostic
      │                                      └─ StopSession
      │
      └──────── EffectFinished ◄──── effect dispatcher
```

`SessionMailbox` has separate normal, lifecycle, and terminal lanes. Accepting an asynchronous effect
first reserves terminal capacity. The reservation prevents unrelated traffic from consuming the
slot needed for that effect's completion. Sequence numbers preserve publication order across lanes.

Refusing admission on the normal lane is a teardown, not a bound: the gateway turns `MailboxFull`
into `CloseRequested("runtime-overloaded")`. So the lane first reclaims what the session no longer
needs — a queued `time-pos` or `mouse-pos` another queued one already supersedes, both properties
whose deltas the projection either never publishes or folds at drain. Anything else still stops the
session, because the alternative is dropping a fact silently.

`SessionReactor` is deterministic: it passes one event at a time to a reducer, dispatches returned
effects, validates completions against the accepted owner and identity, and rejects stale connection
epochs. A reservation permits at most one terminal publication. Closing rejects new work and emits
or directly reduces cancellation outcomes for effects still pending before the mailbox is closed.

`TimerScheduler` uses named timer identities and produces ordinary completion events. Replacing or
cancelling a timer therefore has the same explicit lifecycle as other asynchronous work.

## Maintained invariants

| Invariant | Current contract |
| --- | --- |
| One live state owner | The `Reader` thread mutates production session and presentation state. Background actors return values; the IPC writer owns transport writes, not domain state. |
| Ordered input | mpv events are drained in arrival order. Conflicting cue observations retire cue-dependent interaction before a later command is dispatched. |
| One observation interpreter | `PlaybackProjection` alone turns raw mpv properties into typed facts, explicit revisions, and deltas. A transport burst has no semantic meaning: split and joined delivery of the same ordered observations converge to the same state. |
| Identity-qualified publication | Annotation, geometry, tooltip, and related background results publish only when their semantic identity is still current. |
| Transactional pixel ownership | The ownership policy and renderer/executor control native visibility and the legacy subtitle surface. Geometry readiness cannot cause a style switch. |
| Correlated IPC | Replies require a known request ID from the current connection epoch; late and unknown replies are discarded. Outbound admission is bounded. |
| Nonblocking startup clear | Interactive readiness does not wait for the startup OSD clear reply. |
| Explicit close | Owned surfaces are removed, new work is rejected, and late identity-qualified results cannot republish closed UI. |
| Closed behavior oracle | `BehaviorTrace` accepts only its enumerated, text-free event, state, and outcome vocabulary. |
| Exact host inventory | The checked-in per-module count of functions accepting `Reader` must match exactly; any difference fails the checker. |
| Independent runtime core | Import-linter forbids `saitenka.runtime` from importing the application or mpv adapters. |
| Reserved terminal publication | The isolated mailbox reserves completion capacity before dispatch and accepts at most one terminal event for each reservation. |
| Effect interpreter ownership | An owner's effects are applied by that owner's adapter, not by the host. A pure reducer or a stateless policy returns effects; the object that interprets them belongs to the feature. Purity relocates impurity, and absent this rule it relocates onto the object being retired — which is what `poe host-mass` measures. |

The executable sources of truth are:

- [`tests/test_runtime_behavior_oracle.py`](https://github.com/serjflint/saitenka/blob/main/tests/test_runtime_behavior_oracle.py)
  for ordered production behavior;
- [`tests/runtime_behavior.py`](https://github.com/serjflint/saitenka/blob/main/tests/runtime_behavior.py)
  for the closed behavior-record vocabulary;
- [`tests/test_reader_host_contract.py`](https://github.com/serjflint/saitenka/blob/main/tests/test_reader_host_contract.py)
  and its [inventory](https://github.com/serjflint/saitenka/blob/main/tests/fixtures/reader_host_allowlist.json)
  for `Reader`-accepting function counts;
- [`tests/test_session_runtime.py`](https://github.com/serjflint/saitenka/blob/main/tests/test_session_runtime.py)
  for mailbox, lifecycle, reconnect, overload, timer, and close contracts;
- [`.importlinter`](https://github.com/serjflint/saitenka/blob/main/.importlinter) for package dependency
  direction.
