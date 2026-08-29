# Post-merge session architecture review

Scope: exact clean artifact `90062a97542ed4b0708299ea9ab8708e94ee73b1`; `app/session` plus direct runtime,
feature, mpv, tests, and docs.

## Verdict

The responsibility migration is complete, and the SessionController/composition refactor is complete
enough to close as a bounded program. No remaining code responsibility appears stranded on
`SessionController`; further decomposition would be a new architecture program, not completion of this
migration. I found no P0 or P1 issues.

## Evidence

- `SessionController` is now a 140-line lifecycle/ordered-turn boundary. Its live path starts, pumps,
  stops, closes, settles `InteractionCoordinator`/`CueCoordinator`, and routes only file-load/user-command
  conjunctions (`src/saitenka/app/session/controller.py`).
- Public post-composition authority is the five-method `LiveSession`; pre-start exceptions are explicitly
  named in `PreparedSession` (`src/saitenka/app/session/factory.py`).
- `SessionGraph` is large—42 collaborators—but it is a frozen composition product, not a public runtime
  capability, and construction publishes no partially bound graph (`src/saitenka/app/session/graph.py`).
- `poe arch-map`: 303 modules, 1,377 import edges, zero real cycles; 21 registered reducers/policies with
  zero branch-affecting ambient reads; 34 closed command rows; no same-shape feature methods left on the
  host.
- The live-turn structural guard forbids new feature authority and graph escape; the repository-wide host
  contract finds zero functions accepting `SessionController`
  (`tests/session/test_session_controller_host_contract.py`).
- Lifecycle truth is explicit: ordinary close records every participant in `CloseLedger`; runtime
  cue-retirement failure raises through the reactor and makes `pump()` return false
  (`src/saitenka/app/session/lifecycle.py`, `tests/session/test_session_connection.py`).
- Focused verification: 86 tests passed across controller boundaries, stateless registration,
  close/failure behavior, profile switching, tooltip ownership, and architecture-map controls. Both
  required architecture-review smoke checks passed.

## Findings

### P2 — The durable claim census is stale after the completed migration

- Scenario: a later cadence treats R1/R2 as live defects and T1 as merely argued, re-investigating or
  redesigning behavior already replaced/enforced.
- Discriminator: T1 now has `poe tooltip-ownership` in `all` with planted writer/constructor controls;
  direct close-ledger tests prove failed participants cannot be reported clean; cue-retirement failure
  propagation is tested through the production turn.
- Age: census rows date from `46b0c2d2` on 2026-08-24; drift was introduced by the 2026-08-29
  completion/closure work.
- Remedy: mark T1 gated; replace R1 with the direct `SessionLifecycle`/`CloseLedger` contract; delete or
  rewrite R2 around the current cue-retirement exception contract.

No P3 findings.

## Claim status

| Claim | Declared | Enforced | True |
| --- | --- | --- | --- |
| P1 profile sole writer | yes | no structural negative-control gate | yes by current source census |
| P2 controller has no profile policy | yes | live-turn/host boundary enforced; thread half not | yes |
| P4 profile application on owner thread | yes | no off-owner negative control | production paths support it |
| T1 tooltip mutable ownership | yes | yes, in `poe all` with planted controls | yes |
| R1/R2 lifecycle truth | census is obsolete | yes through focused tests | yes in current design |
| S1 forwarding latency | yes | no | not verified |

## Remaining work classification

- Migration/assurance debt, non-blocking: refresh the census; add a profile writer-census control and an
  off-owner profile-application negative control if those argued invariants are worth permanent
  enforcement.
- Independent future improvement: split or simplify the 1,135-line composition builder, narrow the widest
  capability records, or reconsider the dual stateful/stateless vocabulary. None is evidence that
  controller responsibility remains unmigrated.
- S1 remains measurement debt: the native integration benchmark now uses a real composed session for
  settlement, but its timed cue/tooltip operations call graph owners directly and do not isolate
  adapter-forwarding overhead.

## Strongest case against the principles

This is one local user and one process, yet maintainers must learn owner slices, routes, reducers, effects,
stateless intents, adapters, endpoints, capability values, assembly, graph, and lifecycle plans. A single
owner-thread loop calling bounded feature controllers directly could preserve identity-qualified worker
publication with fewer concepts.

I still land in favor of the current core principles: ordered owner-thread settlement, bounded feature
owners, stale-result refusal, and explicit teardown directly protect hover/mining correctness and playback
stability. The appropriate restraint is to stop the migration now and require new machinery to justify a
new ordering/state/lifecycle need.

## Could not verify

| Claim | What would settle it |
| --- | --- |
| P1 profile sole writer remains stable | A structural writer-census control with a planted second writer |
| P4 all profile application is owner-thread confined | An off-owner negative control at the application boundary |
| S1 forwarding latency is negligible | A benchmark isolating adapter-forwarding overhead from cue and tooltip work |
