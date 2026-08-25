# Interactive runtime

Saitenka has two runtime packages with different jobs:

- `saitenka.app.runtime` holds the per-command policy `SessionController` consults before dispatch: who owns
  a command, whether it needs a current cue, whether it survives with the help overlay open.
- `saitenka.runtime` holds the runtime itself — events, effects, mailbox admission, effect
  lifecycle, timers, the owner slices and the session loop. `app/session/routes.py` composes it, and
  a session with a gateway is driven from it.

This page describes both as they exist on `main`, and is the *how*: where to put a new feature and
what will fail you if you put it elsewhere. Why the runtime is split this way is in
[Architecture](architecture.md#composition-and-extension-seams);
the whole-system module map remains in [Architecture](architecture.md); native subtitle ownership and
its supported operating envelope are documented in
[Native mpv subtitles](../usage/native-subtitles.md).

## Adding a feature

Paths are relative to `src/saitenka/`. The host is `SessionController`, in
`app/session/controller.py`.

Feature-owned policy, adapters, controllers, and presentation live together under
`app/features/<feature>/`. Shared interaction contracts live under `app/interaction/`; session
assembly and cross-feature conjunctions live under `app/session/`. A shared domain value stays in
`app/` when application services outside one feature consume it.

**Does it need a place to remember that does not exist yet?** Not "does it have state" — a toggle
obviously does. An adapter may read *and write* the host members it declares, so a feature whose
flag already lives somewhere is stateless. Only one that would have to create a new home for state
is stateful, and it creates it in a slice.

**No — every fact it needs already has a home.**

1. `app/features/<feature>/<feature>_intents.py` — the pure policy `reduce(command, inputs)`, a frozen
   `<Feature>Inputs`, a closed `<Feature>Command` StrEnum, one dataclass per effect. Import nothing
   that touches mpv, the display, or the host.
2. `app/features/<feature>/<feature>_adapter.py` — a `<Feature>Host` `Protocol` naming exactly the members you touch,
   and an adapter over it exposing `inputs()` and `apply(effect)`.
3. `app/session/routes.py` — a typed row in `stateless_features()` and one explicit adapter argument.
   `StatelessBinding` checks the command, input, and effect types before the heterogeneous
   `StatelessRouter` boundary.

If the feature already has a bounded controller, its adapter protocol names that controller's
surface instead, as the profile row does. Do not re-expose the controller's facts and acts on
`SessionController` merely to satisfy an adapter protocol.

**Yes — it needs a new place to remember.**

1. `runtime/<feature>.py` — `reduce(state, event) -> ReduceResult` (`runtime/state.py`) and the
   state dataclass, under the `Owner` (`runtime/effects.py`) whose slice it belongs to.
2. `app/feature_bindings.py` — declare its reducer, initial-state and local-store factories plus
   the events it accepts, then place it in the owner's explicit order.
3. `app/session/routes.py` — route the owner's declared event vocabulary. Runtime and no-runtime
   construction consume the same feature bindings.

**If a key triggers it**, three more edits, all required:

- `app/bindings.py` — the `*_MSG` script-message constant **and** a `BindingSpec` row in `BINDINGS`
  with a `key_attr`. The constant alone binds no key and shows nothing in the help overlay.
- `app/runtime/commands.py` — a spec row. Not optional: `CommandExecutor` refuses at construction
  if a handler has no spec. Commands are cue-dependent by default; `_CUE_INDEPENDENT` opts out and
  `_HELP_COMMANDS` allows the command while help is open.
- `app/session/controller.py` — one row in `SessionController._build_command_router`. Use
  `interaction(YourCommand.X)`, which routes through the stateless router;
  `action(SessionController.verb)` is the older form and costs a new `SessionController` member,
  which `poe host-mass` refuses.

The following gates enforce the boundary:

| gate | what tripped it |
| --- | --- |
| `tests/session/test_stateless_registration.py` | An `app/*_intents.py` exposing `reduce` that nothing registers. |
| `tests/session/test_session_controller_host_contract.py` | A function under `app/` takes a `SessionController` **parameter**. Declare a `Protocol`. |
| `poe host-mass` | You added a **member** to `SessionController` — a different subject from the row above, which counts parameters. New state belongs in a slice. |
| `poe reducer-purity` | A **registered** stateful reducer branches on something outside `(state, event)`. Stateless policies are not in the route table and are not measured. |
| `poe arch` | Feature packages import session composition, interaction primitives import upward, or feature packages form a runtime cycle. |
| `poe app-package-layout` | A declared feature package disappears, an undeclared package appears, or a retired flat module/import returns. |

## Production session

`SessionController` owns owner-thread session lifetime, cross-feature ordering, and application.
Bounded controllers own feature state and policy: `TooltipController` owns tooltip interaction facts,
presentation state, and work protocols, while `ProfileController` owns the active reading environment
and switch transaction.
`SessionController.run()` hands the thread to
`SessionLoop`, which blocks on the mailbox rather than waking at a cadence:

```text
mpv JSON IPC ──► background reader
                       ├─ events ──► mailbox ──► SessionLoop.receive()
                       └─ replies ─► correlated futures ──► EffectCorrelator

SessionLoop.receive(timeout bounded by the earliest armed timer)
          │  one envelope at a time, in mailbox sequence
          ├──────────────────────┐
          ▼                      ▼
     SessionReactor.handle    SessionController's turn, unless the reactor claimed it
     (owner slices, effects)   ├─ reconcile subtitles
                               ├─ apply results
worker result queues ─────────►└─ update interaction

SessionController and other callers ──► bounded command queue ──► sole writer ──► mpv JSON IPC
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

`CommandExecutor` splits the command table in two: `CommandPolicy` holds the closed spec set
(`app/runtime/commands.py`) and rejects a duplicate name; `SessionController` supplies the bound handlers and
the executor refuses any handler with no spec. So ownership and cue eligibility are decided
without a session, and the composition seam adds no second state owner.

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
observations. `SessionController` hands it one ordered observation at a time and applies the typed deltas it
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

Every fact the projection sees is published; `LEGACY_OWNED` in `runtime/playback.py` is the
withhold-list for facts a downstream owner has not taken yet, and it is empty.

## The runtime contracts

`saitenka.runtime` drives the production session: `app/session/routes.py` installs the reactor and
registers the feature reducers it dispatches to (`poe arch-map` prints the live owner → feature →
event table, which is the count rather than a figure kept here). What the package still *is* —
definable and testable independently of `SessionController`, mpv, Pillow, libass, SQLite and Anki — is the
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
into `CloseRequested("runtime-overloaded")`. So the lane first reclaims a queued observation that a
later queued one already supersedes — value-latest properties only, enumerated in `mailbox.py`.
Anything else still stops the session, because the alternative is dropping a fact silently.

`SessionReactor` is deterministic: it passes one event at a time to a reducer, dispatches returned
effects, validates completions against the accepted owner and identity, and rejects stale connection
epochs. A reservation permits at most one terminal publication. Closing rejects new work and emits
or directly reduces cancellation outcomes for effects still pending before the mailbox is closed.

`TimerScheduler` uses named timer identities and produces ordinary completion events. Replacing or
cancelling a timer therefore has the same explicit lifecycle as other asynchronous work.

## Maintained invariants

| Invariant | Current contract |
| --- | --- |
| One live state owner | The `SessionController` thread applies production session and presentation mutations. Bounded controllers own feature state and policy; background actors return values. The IPC writer owns transport writes, not domain state. |
| One mining writer | `MiningController` alone owns the selected target, deck-derived index, seed/probe lifecycle, local store, scratch resources, and operation admission. `poe mining-ownership` rejects shadow fields and direct mutator/construction escape routes. |
| Ordered input | mpv events are drained in arrival order. Conflicting cue observations retire cue-dependent interaction before a later command is dispatched. |
| One observation interpreter | `PlaybackProjection` alone turns raw mpv properties into typed facts, explicit revisions, and deltas. A transport burst has no semantic meaning: split and joined delivery of the same ordered observations converge to the same state. |
| Identity-qualified publication | Annotation, geometry, tooltip, and related background results publish only when their semantic identity is still current. |
| Transactional pixel ownership | The ownership policy and renderer/executor control native visibility and the legacy subtitle surface. Geometry readiness cannot cause a style switch. |
| Correlated IPC | Replies require a known request ID from the current connection epoch; late and unknown replies are discarded. Outbound admission is bounded. |
| Nonblocking startup clear | Interactive readiness does not wait for the startup OSD clear reply. |
| Explicit close | Owned surfaces are removed, new work is rejected, and late identity-qualified results cannot republish closed UI. |
| Closed behavior oracle | `BehaviorTrace` accepts only its enumerated, text-free event, state, and outcome vocabulary. |
| Exact host inventory | The checked-in per-module count of functions accepting a `SessionController` **parameter** may not grow. The census is currently empty, so any such function under `app/` fails; a removal tightens the baseline in place rather than failing. Separate from `poe host-mass`, which counts `SessionController`'s **members**. |
| Independent runtime core | Import-linter forbids `saitenka.runtime` from importing the application or mpv adapters. |
| Reserved terminal publication | The isolated mailbox reserves completion capacity before dispatch and accepts at most one terminal event for each reservation. |
| Effect interpreter ownership | An owner's effects are applied by that owner's adapter, never by the host. Both layers return effects; the object that interprets them belongs to the feature. |
| Stateless registration | Every `app/*_intents.py` policy is reachable only through `StatelessRouter`, and its adapter's coupling to the host is declared rather than implicit. |

The executable sources of truth are:

- [`tests/session/test_runtime_behavior_oracle.py`](https://github.com/serjflint/saitenka/blob/main/tests/session/test_runtime_behavior_oracle.py)
  for ordered production behavior;
- [`tests/runtime_behavior.py`](https://github.com/serjflint/saitenka/blob/main/tests/runtime_behavior.py)
  for the closed behavior-record vocabulary;
- [`tests/session/test_session_controller_host_contract.py`](https://github.com/serjflint/saitenka/blob/main/tests/session/test_session_controller_host_contract.py)
  and its [inventory](https://github.com/serjflint/saitenka/blob/main/tests/fixtures/session_controller_host_allowlist.json)
  for `SessionController`-accepting function counts;
- [`tests/session/test_session_runtime.py`](https://github.com/serjflint/saitenka/blob/main/tests/session/test_session_runtime.py)
  for mailbox, lifecycle, reconnect, overload, timer, and close contracts;
- [`tools/mining_ownership_check.py`](https://github.com/serjflint/saitenka/blob/main/tools/mining_ownership_check.py)
  for the mining writer boundary and its planted controls;
- [`.importlinter`](https://github.com/serjflint/saitenka/blob/main/.importlinter) for package dependency
  direction.
