# Completed `SessionController` responsibility migration

## Scope

Post-merge review of the session composition boundary at `d0bd53cc`. This closes the bounded
`Reader`/`SessionController` responsibility-migration program; it is not a whole-repository fitness
verdict.

## Verdict

The responsibility migration is complete. `SessionController` is now a small owner-thread boundary:
it pumps accepted session work, delegates cue settlement to the composed graph, orders lifecycle
start/close, and reports failures. Feature policy, mutable feature state, background-work admission,
and presentation decisions belong to feature owners or session collaborators.

This does not prove that every resulting owner is optimally sized or that the session package cannot
be simplified further. Builder decomposition, capability narrowing, and owner-specific reviews are
independent architecture work, not unfinished migration compatibility.

## Evidence

- `src/saitenka/app/session/controller.py` is 140 lines; the former 4,574-line controller and its
  migration facades, host-taking functions, codemods, and host-mass ratchets are gone.
- `SessionGraph` is a frozen composition value. The host-contract test refuses feature policy on the
  shell, aggregate escape, aliases, and passing the controller into feature owners.
- `poe arch-map` reports 303 modules, 1,377 import edges, no real cycles, 34 command rows, 14 stateful
  reducers, and seven stateless policies.
- `poe reducer-purity-census` reports 21 registered reducers with no branch-affecting injected reads.
- `poe port-probe-census` reports no dead probes; unresolved protocol receivers remain review evidence,
  not a proof of unsafe authority.
- The exact merged head passed the full deterministic and free-threaded gates. The locked native
  benchmark exercised 101 cues through the composed session runtime.

## Claim-census delta

- `T1` is gated by `poe tooltip-ownership` and planted negative controls.
- Obsolete reactor-era close claims `R1` and `R2` now name the direct `SessionLifecycle` ledger and
  cue-retirement failure contracts, both covered by scenario tests.
- `P2` is narrowed to the structurally gated shell boundary. The separate owner-thread claim `P4`
  remains argued.
- `P1`, `P4`, and `S1` remain argued: sole profile-state writing, universal owner-thread application,
  and negligible forwarding latency still lack their stated discriminators.

## Remaining risks

- Profile ownership is behaviorally strong but lacks a structural writer census and an off-owner
  negative control.
- No isolated measurement attributes owner-thread latency to the forwarding seams.
- Large feature owners may merit their own scoped reviews; their size alone is not migration debt.

