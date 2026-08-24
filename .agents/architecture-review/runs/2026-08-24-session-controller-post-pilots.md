# Post-pilot `SessionController` review

## Integration supplement

The isolated report below remains pinned to the tree it reviewed:
`87055d330f0e9f9c22b7ae93d2860d4f5e3cdfd2`. This artifact was integrated on
`origin/main` `4dce381189dc0c89cd5c3efe4f9414406eeb0a84`; the intervening packaging merge changes none
of the seven scoped source modules. The integration-tree gate passed 4,104 default-tier tests,
4,107 free-threaded tests, and coverage above the 85% floor. The updated claim census contains nine modules,
34 claims, and 20 argued rows.

### Additional P1 — runtime-owned close failure is recorded as a performed phase

- **Axis:** Failure modes and resilience; lifecycle ownership; observability.
- **Failure scenario:** `session_routes._retire()` isolates a resource exception and returns `False`,
  but the fire-and-forget branch in `SessionReactor` discards the dispatcher's result.
  `SessionController` then records only that `SessionClosing` was delivered. A runtime-owned phase
  can consequently be reported as performed, suppressing its local fallback, even though one of
  its resources failed to close. This can leave the resource alive while the close ledger appears
  clean.
- **Discriminator:** A direct reproduction with a throwing runtime-owned resource made `_retire()`
  log the exception while the close report contained no failure and every phase was recorded as
  performed.
- **Age:** The fire-and-forget result discard began in `c3ada21d` on 2026-08-19; `_retire()` acquired
  its boolean outcome in `29cb5003` and performed-phase recording arrived in `3dfeede6`, both on
  2026-08-20. The complete harmful chain therefore existed by 2026-08-20 and predates the reviewed
  pilots.
- **Remedy:** Census rows R1 and R2 carry the settlement contract: propagate lifecycle-performer
  outcome into close accounting and prove with a throwing-resource negative control that the phase
  cannot be reported clean.

### Pilot-delta coordinates

The reviewer report's before/after table headings repeat the pilot commit because they name the
comparison, not both endpoints. The reproducible ranges are:

- tooltip pilot: `a3ad93abcc4f26b9548a585b52742aa9a9f83577` →
  `60360e8860a48250941d2c06a8f4b0c3a38bde89`;
- profile pilot: `ffae3004a7fb7a5d2a46eaee851ac3d490bdd862` →
  `944ed1ea06d4db282d694f2f2ac47cf973cba529`.

---

# Isolated reviewer report (verbatim)

Scope: scoped post-migration review of `src/saitenka/app/session_controller.py` and its direct profile, tooltip, session-adapter, routing/reactor/loop collaborators at detached `origin/main` `87055d330f0e9f9c22b7ae93d2860d4f5e3cdfd2`; this is not a whole-repository verdict.

# Verdict

The runtime foundations are sound for Saitenka’s product: one ordered owner thread, bounded/correlated off-thread work, identity-qualified publication, and an in-mpv hot path. The tooltip pilot found a boundary that earns its cost.

The profile pilot did not yet establish a complete active-environment owner. A live profile switch leaves the mining target behind and can later accept a stale launch-profile dictionary result. The first can write a card to the wrong deck/model/field map; the second can silently undo the selected profile’s dictionary scope. Therefore this tree is not fit to universalize the profile pilot’s shape, and `SessionController` cannot yet be described as a thin owner-thread kernel. It remains a 4,574-line composition-and-application host with 342 members, 180 substantive methods, 2,167 substantive lines, and a 452-line constructor.

The full deterministic gate passed, including 4,067 default-tier tests, 4,070 free-threaded tests, import contracts, architecture ratchets, and 92.13% coverage. The focused architecture set passed 107 tests. Those green results do not exercise the two failures below.

# Evidence

## Product yardstick

The product goals were derived from `README.md`, `docs/index.md`, `docs/usage/features.md`, `docs/usage/configuration.md`, the generated CLI reference, `ARCHITECTURE.md`, and the current code:

- Saitenka is a local-first, single-user, single-session immersion tool embedded in mpv’s own video surface.
- The watch → hover → grounded dictionary → mine loop must remain immediate and must not stall or destabilize playback.
- Readings and pitch must remain dictionary-grounded.
- A reading profile represents the active language environment, including tokenizer, language-specific dictionaries, subtitle track/font behavior, and a profile-scoped mining target.
- Off-thread annotation, geometry, dictionary, tooltip, and rendering work must publish only when its semantic identity is still current.
- Optional mpv, Anki, dictionary, and rendering integrations must degrade predictably rather than crash or silently mutate the wrong state.
- Architecture machinery earns its cost only where it protects these properties; there is no distributed, multi-tenant, replay, or public session-plugin requirement.

## Exact tree

- Reviewed commit: `87055d330f0e9f9c22b7ae93d2860d4f5e3cdfd2`
- Repository state before and after review: clean
- No source, test, documentation, or architecture artifact was edited.

## Architecture meters

`uv run poe arch-map` reported:

- 260 modules
- 1,036 import edges
- three reported cycles, all annotation-only
- zero real import cycles
- 18 registered stateful reducers
- 37 command rows
- 34 command rows resolving to a `SessionController` verb
- seven registered stateless policy modules
- stateless host widths including `SessionHost` at 10 members and `ProfileHost` at 3

The live ownership table was:

| owner | events | features |
| --- | ---: | --- |
| `session` | 10 | `startup-hint`, `lifecycle-close`, `lifecycle-start`, `connection`, `episode`, `user-command` |
| `playback` | 7 | `playback` |
| `subtitle` | 7 | `subtitle-tracks` |
| `interaction` | 33 | `hover`, `help`, `picker`, `sidebar`, `tip-nav`, `copy-pulse`, `hover-pause`, `hovered-word`, `card-preview` |
| `presentation` | 3 | `translation` |

`uv run poe runtime-status` reported:

- zero runtime debt symbols
- 21 of 21 lifecycle duties migrated

`uv run poe host-mass-census` reported:

- total members: 342
- substantive methods: 180
- substantive lines: 2,167
- constructor lines: 452
- delegators: 98
- self-delegators: 6
- derived properties: 46
- slice properties: 11
- aliases: 1

`uv run poe host-arity-over` reported zero functions over the eight-argument ceiling, over a denominator of zero remaining host-taking functions.

`uv run poe port-probe-census` reported:

- 54 total probes
- zero dead probes
- one live capability check
- 53 unresolved receivers
- zero argued

`uv run poe reducer-purity-census` reported:

- 18 registered reducers
- zero ambient/injected readings reaching a branch
- two injected readings used only to stamp effects
- zero argued

Its own output explicitly limits the claim to registered route-table reducers; stateless decision functions are outside the census.

## Meter limitation reproduction

The architecture-review skill prescribes:

```text
uv run poe cluster-map
```

The command exits with:

```text
usage: cluster_map.py [-h] [--member NAME] [--json] [modules ...]
cluster_map.py: error: name a module, or pass --member
```

Invoking `tools/cluster_map.py` directly for the relevant new shapes reported:

```text
## profile_adapter.py: {'error': 'no host-taking functions'}
## session_adapter.py: {'error': 'no host-taking functions'}
## session_routes.py: {'error': 'no host-taking functions'}
## tooltip_controller.py: {'error': 'no host-taking functions'}
```

Inspection of `tools/cluster_map.py` showed that its module maps are built from `host_arity.collect()`, so adapter protocols, bounded-controller attributes, and retained bound callbacks disappear when the explicit `SessionController` parameter disappears.

## Pilot deltas

Before and after the tooltip-controller pilot:

| meter | before `60360e88` | after `60360e88` |
| --- | ---: | ---: |
| total host members | 344 | 342 |
| substantive methods | 187 | 181 |
| substantive lines | 2,353 | 2,211 |
| constructor lines | 459 | 453 |

The pilot added a 331-line `TooltipController` while retiring six substantive host methods and 142 substantive host lines. The owner now holds three real volatile-work protocols plus shared tooltip state and lifecycle.

Before and after the profile-controller pilot:

| meter | before `944ed1ea` | after `944ed1ea` |
| --- | ---: | ---: |
| total host members | 342 | 342 |
| substantive methods | 181 | 180 |
| substantive lines | 2,208 | 2,167 |
| constructor lines | 453 | 452 |

The pilot added:

- `profile_controller.py`: 208 lines
- `profile_adapter.py`: 39 lines
- `profile_intents.py`: 32 lines

It retired one substantive host method and 41 substantive host lines while leaving total host membership unchanged.

## Structural profile census

A current-tree assignment census found writes to:

- `_profile`
- `_profiles`
- `_profile_index`
- `_tokenizer`
- `_dict_set`

only in `src/saitenka/app/profile_controller.py`.

Current production mutator call sites include:

- `configure_cycle()` from run and attach assembly
- `switch_to()` through `ProfileAdapter`
- `replace_dictionary_set()` from `SessionController._install_collaborators()`
- test-only direct tokenizer/dictionary substitutions

This establishes current truth but not enforcement: no structural writer census with a planted outside write exists.

## Live mining-target reproduction

An in-process reproduction:

1. Constructed a headless `SessionController`.
2. Stored a sentinel object as `reader.mine_cfg`.
3. Configured default and French profiles.
4. Called the real `reader.cycle_profile()` path.
5. Printed the selected profile and whether `mine_cfg` retained identity.

Output:

```text
fr True
```

The active profile changed to French while the launch mining configuration remained the exact same object.

The production path then confirms the consequence:

- `SessionController.miner_ports` reads `self.mine_cfg`.
- `miner.py` reads that object for deck, model, fields, dedupe, note construction, media policy, and word-audio fields.

## Late dependency evidence

`tests/test_profile_switcher.py::test_late_dependency_result_keeps_the_existing_last_arrival_dictionary_policy` performs:

1. configure a profile cycle whose French scope returns `active_dicts`;
2. cycle to French;
3. inject a late dependency result containing `late_launch_dicts`;
4. assert the profile remains French;
5. assert the active dictionary set is now `late_launch_dicts`.

The passing test therefore proves that dependency publication is not qualified by profile identity and explicitly blesses the overwrite.

## Verification runs

Focused architecture tests:

```text
107 passed, 1 warning
```

Covered:

- profile switching and preflight
- profile intents
- tooltip metadata owner-thread publication
- off-thread tooltip opens
- render-ahead wiring
- stateless registration
- `SessionController` host contract
- runtime behavior oracle
- session runtime

Required smoke checks:

```text
architecture-review smoke OK
claim-census: OK 5 modules censused, 27 claims, 14 argued
architecture-review loop smoke OK
```

Full deterministic gate:

- lint and formatting: clean, 799 files unchanged
- mypy: clean
- basedpyright: clean
- import-linter: 10 contracts kept, zero broken
- ast-grep invariant controls: green
- tool tests: 362 passed
- main suite: 4,067 passed, 35 skipped, 1 xfailed
- free-threaded suite: 4,070 passed, 32 skipped, 1 xfailed
- coverage: 92.13%
- dependency, documentation, skill, corpus, runtime-migration, host-arity, host-mass, port-probe, and reducer-purity gates: green
- final worktree: clean

# Findings table

| Priority | Finding | Axis | Age |
| --- | --- | --- | --- |
| P0 | A live profile switch keeps mining into the launch profile’s target | Fitness for purpose; soundness; failure modes | Since live switching, `4db0f0dd`, 2026-08-09; preserved by the 2026-08-24 profile pilot |
| P1 | A late dependency result can overwrite the selected profile’s dictionary set | Failure modes; soundness; testability | Behavior predates the pilot; current host form `a2e83da8`, 2026-08-21; explicitly blessed by `944ed1ea` |
| P2 | The pilots establish owners, but not a thin owner-thread kernel | Coupling; cohesion; simplicity; evolvability | Longstanding large host; bounded-controller evidence is new in the two 2026-08-24 pilots |
| P2 | The architecture census cannot inspect the new adapter/controller shape | Soundness; testability; evolvability | `cluster_map` introduced in `3efcf781`, 2026-08-21; blind spot exposed by the 2026-08-24 pilots |

No P3 findings were promoted.

# Detailed findings

## P0 — A live profile switch keeps mining into the launch profile’s target

- **Axis:** Fitness for purpose; soundness of invariants; failure modes.
- **Failure scenario:** A user launches under the Japanese profile, cycles live to French, then mines a French word. The visible profile, tokenizer, font, subtitle track, and dictionary set switch, but `SessionController.mine_cfg` does not. `miner_ports` passes that unchanged config to mining, and `miner.py` uses it for the deck, model, fields, dedupe query, media policy, and note construction. The card can therefore be added to the Japanese deck/model/field map even though the UI reports the French profile.
- **Declared / enforced / true:** The docs declare that a profile owns its mining target and that live cycling is “the same full switch as launching” (`docs/usage/configuration.md:244-251`, `docs/usage/features.md:134-147`). Launch-time profile scoping does build the correct `MineConfig`. No test cycles live and then mines against a profile-specific target. In the current code, `ProfileController.switch_to()` has no mining capability or mining configuration, while `SessionController.miner_ports` reads `self.mine_cfg`.
- **Discriminator:** An in-process reproduction switched the active profile to `fr` while preserving exact object identity of the launch mining config: `fr True`. The innocent explanation “mining resolves the target lazily from the active profile” is falsified by `session_controller.py:2742-2770`; it reads the stored `self.mine_cfg`.
- **Age:** The omission has existed since the live switcher was introduced in `4db0f0dd` on 2026-08-09. The “same full switch” promise was added in `33713493` on 2026-08-11. The 2026-08-24 profile-controller pilot preserved it.
- **Remedy:** Make the profile transaction cover the complete profile-scoped runtime environment, including the effective `MineConfig` and any derived mined-seed/dedupe state. Preflight the new configuration before commit, then invalidate/reseed the old deck-derived state in the same owner-thread turn. Add an observable test that cycles through the real command path and mines through fake Anki, asserting the resulting note’s deck/model/fields. If live mining-target switching is intentionally unsupported, remove the “same full switch” claim and explicitly disable mining after a live switch rather than silently using the old target.

## P1 — A late dependency result can overwrite the selected profile’s dictionary set

- **Axis:** Failure modes and resilience; soundness of invariants; testability.
- **Failure scenario:** Interactive startup begins building dependencies for the launch profile. Before that build completes—especially plausible in attach/plugin mode or a first dictionary build—the user cycles to another profile. `ProfileController.switch_to()` installs the new profile-scoped dictionary set. When the old build lands, `_install_collaborators()` unconditionally calls `profile_controller.replace_dictionary_set(deps["dict_set"])`; the active profile remains new while its dictionary set becomes the launch profile’s. Hover can then miss, show the wrong language’s definitions, or mine with a mismatched source.
- **Discriminator:** `tests/test_profile_switcher.py:343-354` constructs exactly this sequence and asserts that the late launch dictionary replaces the active profile’s dictionary. That rules out the innocent explanation that dependency completion is identity-qualified or re-scoped at publication. `reader_deps.DepsLoad` carries only an unqualified `dict`, and `_install_collaborators()` performs last-arrival-wins.
- **Age:** The unconditional collaborator installation predates the pilot; its current host-owned form dates to `a2e83da8` on 2026-08-21. The profile pilot in `944ed1ea` added a test that codifies the overwrite instead of rejecting it.
- **Remedy:** Stamp dependency results with the profile identity or generation captured when building. On the owner thread, either discard a result whose identity is stale or re-scope its dictionary set for the currently active profile before publication. Replace the current characterization with a negative control proving an old-profile result cannot overwrite a newer selection.

## P2 — The pilots establish owners, but not a thin owner-thread kernel

- **Axis:** Coupling and cohesion; simplicity versus accidental complexity; evolvability.
- **Failure scenario:** A maintainer follows the new pattern for another bounded feature. They add a controller, intent vocabulary, adapter, registration row, and typed callback/port bundles while leaving the host’s application capabilities and construction graph intact. Every ratchet remains green, yet the maintainer must understand both the new owner and the same central host. Repetition yields a federation of typed forwarding seams around a god object rather than a smaller kernel.
- **Discriminator:** The profile pilot’s exact host delta was `total 342→342`, `substantive 181→180`, `substantive_lines 2208→2167`, `init_lines 453→452`, while adding `profile_controller.py` (208 lines), `profile_adapter.py` (39), and `profile_intents.py` (32). The tooltip pilot did retire more host behavior—`total 344→342`, `substantive 187→181`, `substantive_lines 2353→2211`, `init_lines 459→453`—and its 331-line controller owns three real volatile-work protocols. The pilots therefore do not support one universal shape: tooltip ownership buys a coherent lifecycle and stale-result boundary; profile’s extra stateless ceremony does not complete the profile aggregate and leaves the host total unchanged.
- **Age:** The large host is longstanding. The bounded-controller vocabulary and the evidence that it can plateau rather than retire the host are new in the two 2026-08-24 pilots.
- **Remedy:** Do not set “has a bounded controller” as the migration outcome. Require each candidate boundary to name the resource, state, policy, and lifecycle it retires from `SessionController`, and meter both the old host and the newly retained callback/capability surface. A next conversion should be priced through `plan-migration`; deciding whether to continue this direction across the system requires whole-system `architecture-inquiry`, not extrapolation from this scoped review.

## P2 — The architecture census cannot inspect the new adapter/controller shape

- **Axis:** Soundness of invariants; testability; evolvability.
- **Failure scenario:** A reviewer or migration author uses the prescribed `cluster-map` to inspect the coupling of a newly extracted adapter or controller. It reports no data, so widening a protocol or retaining bound host callbacks can look like completed decoupling. `host-arity` also reads zero because the explicit `SessionController` parameter is gone; `host-mass` only prevents new host members. The exact seam introduced by the pilots is therefore outside the detailed structural census.
- **Discriminator:** The documented bare command `uv run poe cluster-map` exits with “name a module, or pass --member.” Invoked directly for `session_adapter.py`, `profile_adapter.py`, `session_routes.py`, and `tooltip_controller.py`, it reports `{'error': 'no host-taking functions'}` for every module. This is not because the modules are uncoupled: `arch-map` separately reports `SessionHost` at 10 members and `ProfileHost` at 3, while `TooltipController` retains `EngagedBuildPorts` and consumes fresh `TooltipApply` port bundles. Inspection of `tools/cluster_map.py` confirms that its module map is built from `host_arity.collect()`, i.e. host-taking functions.
- **Age:** `cluster_map` was promoted in `3efcf781` on 2026-08-21. The blind spot became correctness-relevant when the 2026-08-24 pilots replaced explicit host parameters with protocols and bounded-controller capabilities.
- **Remedy:** Either narrow the tool’s documented claim to legacy host-parameter migrations or extend it to classify structural protocol members, bounded-controller attributes, dataclass callback ports, and bound-method capabilities. Add a planted control using the profile and tooltip shapes. Fix the architecture-review invocation to supply modules or make the bare task emit a useful default census.

# What is genuinely good

- `saitenka.runtime` is genuinely independent: `arch-map` reports 260 modules, 1,036 import edges, three annotation-only cycles, and zero real cycles; import-linter keeps the runtime→app/mpv boundary closed.
- The ordered runtime is not decorative. `SessionLoop` asks whether a terminal is claimed before the reactor retires it, preserves mailbox sequence, bounds waits by the earliest timer, and hands only unclaimed payloads to the legacy turn. `SessionReactor` reserves terminal capacity, rejects reused effect IDs, qualifies completions by owner/identity/connection epoch, and drains cancellation before closing.
- The tooltip pilot found a boundary worth defending. `TooltipController` owns tooltip presentation state plus metadata, engaged-build, and render-ahead work state; it centralizes admission, stale refusal, fallback, publication, cancellation, and close. `test_metadata_completion_applies_on_the_owner_thread` proves worker resolution occurs off-thread while publication returns to the owner thread. The focused tooltip tests cover stale failures and off-thread opens.
- The profile pilot did make current writer ownership legible. A current-tree assignment census finds `_profile`, `_profiles`, `_profile_index`, `_tokenizer`, and `_dict_set` written only inside `ProfileController`; tokenizer and dictionary preflight happen before the main profile fields change.
- The full gate is broad and healthy. Runtime debt is zero, 21 lifecycle duties are represented, 18 registered reducers have zero deciding ambient reads, the host and port ratchets are live rather than vacuous, and both default and free-threaded suites pass.

# Strongest case against the current architecture

Saitenka is one local process, one user, and one live session. It has no multi-tenancy, replay consumer, distributed consensus, or public session-plugin API. A simpler architecture could keep one event loop, direct feature-controller methods, and explicit background-job identities. That would remove the dual stateful/stateless vocabulary—owner, slice, route key, reducer, internal event, effect, stateless intent, adapter, host protocol, capability bundle—from many commands.

For trivial profile cycling, the machinery costs more concepts than the policy contains. `ProfileInputs`, `SwitchProfile`, `ProfileAdapter`, the registration row, and the `SessionController.cycle_profile` forwarding method ultimately compute “if more than one profile, increment modulo count” and call the controller that already owns both facts. The same extraction left the host at 342 members and failed to capture the profile’s declared mining target.

The current migration gates can reward appearance rather than retirement:

- `host-arity` reaches zero once explicit host parameters become protocols.
- `cluster-map` then loses the ability to inspect the new module.
- `host-mass` prevents growth but accepts a plateau.
- port width is printed but not gated.
- a bounded controller can retain bound methods back into the host.
- the old host and new owner can both remain necessary.

The strongest simpler alternative is therefore:

- retain the one owner-thread session loop;
- use direct bounded controllers only for cohesive resources or feature state;
- keep explicit identities for concurrent work;
- keep typed values at real cross-module boundaries;
- let trivial owner methods remain direct rather than forcing every command through a reducer/effect/adapter trio.

That alternative would reduce vocabulary and forwarding while preserving the product’s essential single-thread and stale-result guarantees.

Its cost is real: without discipline, cross-feature ordering can drift back into statement sequences, controller methods can accumulate policy, and background work can regain ad hoc lifecycle handling. The current runtime was built in response to those failures, so returning to an unconstrained monolith would be worse.

# Strongest case for the current architecture

Saitenka’s process topology may be local, but its failure modes are genuinely concurrent:

- mpv events arrive from a blocking reader thread;
- outbound IPC has a separate physical writer;
- annotation, tooltip metadata, tooltip builds, render-ahead, geometry, providers, analysis, capabilities, caches, and rendering can all complete after their originating cue, profile, connection, viewport, or session has changed;
- teardown must cancel and join work without leaving mpv, the Python process, or an executor alive;
- a stale reply or stale visual result can publish plausible but wrong UI;
- a missed terminal reservation can hang a request forever.

The mailbox/reactor/correlator model directly addresses these product failures. It establishes:

- one ordered event consumer;
- explicit connection epochs;
- reserved terminal capacity;
- unique effect identities;
- stale-result classification;
- bounded overload behavior;
- deterministic lifecycle phases;
- controlled fallback for non-runtime screenshot and test sessions;
- a runtime package independent of mpv and the application.

The stateful reducer shape also earns its cost where several features share an owner and respond to overlapping events. `SliceReducer` lets a second feature register without rewriting the first, and the purity census prevents ambient facts from silently deciding a turn.

The tooltip pilot is the strongest local evidence for bounded controllers. Tooltip state and three asynchronous protocols share admission, supersession, fallback, owner-thread publication, cancellation, and close. Keeping them on `SessionController` duplicated lifecycle and stale checks; moving them together creates a real failure boundary.

The architecture is therefore not cargo-culted distributed machinery in its runtime core. Its strongest concepts correspond to actual concurrency and lifecycle problems in the product.

# Principles answer

The principles are right, but the sizing rule is not.

Load-bearing principles:

- one owner thread for visible session mutations;
- ordered observations and immediate cue retirement;
- bounded queues and explicit overload;
- correlated effects qualified by identity and connection epoch;
- background values applied only after current-identity validation;
- explicit teardown that cannot silently abandon workers or surfaces;
- pure reducers where ordering or refusal policy is nontrivial;
- independent runtime-core dependency direction;
- bounded owners for cohesive state plus lifecycle or concurrent work.

Principles that should not be universal:

- every stateless command needs its own reducer/effect/adapter trio;
- every extraction called a bounded controller is progress;
- protocol-shaped forwarding is sufficient decoupling;
- a no-growth ratchet is evidence that the kernel is becoming thin;
- a feature aggregate can be named “owned” while profile-qualified collaborators remain outside it.

The tooltip owner clears the product test: it isolates concurrency, stale publication, and lifecycle. The profile owner does not yet clear it because the product’s declared profile aggregate includes mining and because startup publication is not profile-qualified.

A complete reducer takeover is not justified from this scoped evidence. It would remove the legacy fallthrough and mutable host, but at high conversion and vocabulary cost, including on latency-sensitive synchronous interactions. A retreat to an unconstrained monolith is also not justified; it would discard real ordering, overload, correlation, and shutdown guarantees.

The appropriate direction is selective:

- keep the runtime core;
- keep the tooltip owner;
- repair the profile aggregate;
- require an explicit retirement result before extracting another bounded controller;
- use architecture inquiry before choosing any whole-system continuation.

# Cut list

- **Cut `ProfileInputs`, `SwitchProfile`, and `ProfileAdapter` if profile cycling remains the only profile command.** Put `cycle()` on `ProfileController` and bind the command table directly to that bounded owner. Loss: uniform stateless registration and a standalone unit test for three lines of modulo admission policy. Gain: one less reducer/effect/adapter vocabulary around an owner that already has the needed state.
- **Replace, rather than preserve, the late-dependency “last arrival wins” characterization.** Loss: documentation of the old behavior. Gain: the active profile becomes a real identity boundary.
- **Do not cut `TooltipController`.** Its state, three concurrent work protocols, generation fences, fallback, and teardown are one cohesive resource boundary.
- **Do not cut the mailbox/reactor/correlator split.** It buys ordered input, bounded backpressure, stale-result refusal, reconnect safety, and shutdown completion—directly relevant to an overlay that must not hang mpv or publish obsolete UI.
- **Do not use `cluster-map`’s current module mode as evidence of decoupling.** Retain its legacy migration value, but cut the broader claim until the new structural shape is measurable.

# Retained complexity

The following complexity earns its place and should survive remediation:

| Complexity | Why it remains |
| --- | --- |
| `SessionMailbox` lanes and terminal reservations | Prevent normal traffic from consuming the capacity required to complete accepted asynchronous work. |
| `SessionLoop` as sole mailbox consumer | Preserves envelope order and combines arrival wakeups with timer deadlines. |
| `SessionReactor` effect ledger | Qualifies completions by effect identity, owner, semantic identity, and connection epoch. |
| `OwnerRouter` and owner slices | Allow overlapping event types and multiple features per owner without a reducer rewrite. |
| Claimed-versus-fallthrough migration seam | Keeps runtime and non-runtime/test sessions operational while duties migrate, with a measurable claim census. |
| Playback projection and revision identities | Prevent raw mpv-property interpretation from spreading across features and retire conflicting cue state immediately. |
| Tooltip controller | Cohesively owns mutable tooltip state, three volatile work protocols, fallback, stale refusal, publication, and teardown. |
| Profile controller after repair | A synchronous preflight-and-commit boundary is appropriate once it owns the complete profile-scoped environment. |
| Typed per-turn port values | Useful where they snapshot facts for one operation and do not become permanent host facades. |
| Import-linter runtime boundary | Keeps the deterministic runtime independently testable from mpv, Pillow, SQLite, Anki, and the application layer. |
| Full default and free-threaded suites | Directly protect the project’s no-GIL and cross-platform execution goals. |

The following complexity is retained only provisionally:

| Complexity | Condition for retaining it |
| --- | --- |
| Stateless reducer/adapter seam | Each policy must contain meaningful order, refusal, or cross-effect decisions; trivial bounded-owner commands should not be forced through it for uniformity alone. |
| Host protocols | Their width and writes must be measurable, and they must replace rather than hide host coupling. |
| `SessionController` fallback branches | Retain only while non-runtime screenshot/test sessions require them; the claim census should keep their denominator visible. |
| Compatibility projections such as `SessionController.tip` | Retain while they prevent wider coupling; retire when callers can use the bounded owner without multiplying forwarding names. |

# Could not verify

| Claim | Why it could not be settled | What would settle it |
| --- | --- | --- |
| P1: `ProfileController` remains the sole writer of active profile, profile index, tokenizer, and dictionary-set facts under future edits. | The current source census supports it, but there is no structural gate and no planted negative control over external assignments or mutator exposure. | Add an AST/ast-grep writer census covering assignments and mutator calls, plant an external write that must fail, then put the gate in `poe all`. |
| P2: Every production profile transaction is applied on the session owner thread. | The normal command path is mailbox→loop→reactor performer→command router, but no profile test records the thread for controller writes and all invalidation/subtitle/aftermath callbacks. Direct controller/adapter calls remain possible. | Start a real `SessionLoop` on a named owner thread, publish `PROFILE_CYCLE_MSG` from a different thread, instrument every transaction callback and state write, and assert all application occurs on the owner thread; demonstrate a negative control that calls the adapter from the worker thread. |
| P2: `SessionController` carries no profile-switch policy beyond narrow capabilities. | Current inspection finds no target-selection or commit policy on the host, but the boundary is semantic: `_select_profile_subtitle_track`, `_retokenize_current_cue`, dependency installation, and cache invalidation can absorb policy without changing their names. | Add a structural census for profile-named host methods plus an observable controller-only test suite whose fake capabilities contain no profile decisions; plant a target-selection branch on `SessionController` and prove the census fails. |
| The typed forwarding seams add no measurable interaction latency. | No performance claim was admitted: the censuses measure structure, not runtime cost, and no live mpv profile was run. | Compare dated live Perfetto/py-spy traces for cue settlement, hover, profile cycling, and scroll against `BENCHMARKS.md`, isolating owner-thread self-time and queue transitions. |
| Tooltip state has no external writer outside `TooltipController` and its delegated presentation helpers. | Behavioral tests cover publication and stale work, but this review did not construct a complete structural writer census over the mutable `TooltipState` graph. | Add a writer census with a planted external mutation, covering `TooltipState`, metadata, engaged, and render-ahead state plus setter exposure. |

# Summary

- The runtime/mailbox/reactor principles are fit for Saitenka and should be retained.
- The tooltip pilot is a successful bounded-owner pilot because it captures a cohesive concurrent resource and retires meaningful host behavior.
- The profile pilot improves current writer locality but does not own the complete declared profile environment.
- P0: live profile switching leaves the mining target at the launch profile and can write cards to the wrong deck/model/fields.
- P1: a late launch-profile dependency result can overwrite the selected profile’s dictionary set.
- `SessionController` is not yet a thin kernel: 4,574 lines, 342 members, 180 substantive methods, 2,167 substantive lines, and a 452-line constructor.
- The current structural meters cannot inspect the protocol/controller callback shape introduced by the pilots.
- Do not universalize either pilot’s mechanics. Preserve the tooltip owner, repair the profile transaction, measure callback/protocol retention, and require actual host retirement before extracting another bounded controller.
- Any decision to continue toward a whole-system controller federation or a complete reducer runtime requires architecture inquiry; this scoped review supplies evidence but does not license that system-wide choice.
