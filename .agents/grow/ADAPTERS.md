# Grow host-adapter contract

`SPEC.md` owns behavior. This file owns the transport boundary between that behavior and an agent host.
The deterministic instruments under `overlay/tools/` (`grow_triage`, `grow_gate`, `grow_ledger`) are the
shared core; a host adapter supplies shell execution, isolated agent invocations, phase reporting, and
optional PR transport. It mirrors `.agents/sharpen/ADAPTERS.md` — read that for the shared rationale; only
the Grow-specific deltas are spelled out here.

## Host operations

| Operation | Contract |
|---|---|
| `exec(command, cwd)` | Run a deterministic command and return exit code plus complete output. |
| `invoke(role, prompt, schema, isolation)` | Start a fresh agent context, validate its result against `contracts.json`, return result + host invocation id. |
| `phase(name, detail)` | Report progress without changing repository state. |
| `record(record)` | Append one JSONL object using `grow_ledger.py` hashing (the semantic `gap_id` + `target_sha`) and the ledger manifest version. |
| `open_pr(body)` | Optional; available only after explicit `openPr=true` and every ship guard passes. Never merge. |

The adapter, not an agent response, assigns invocation identity. If the host exposes no id, a unique
adapter-assigned label is acceptable only when the host structurally guarantees each call is a fresh
context; otherwise fidelity is unproven and the run is `dry-run`.

## Agent roles

- **Author:** generation-capable; may edit the single target test file (ADDITIVE only — see below).
- **Skeptic:** independent adversarial verifier; read-only except inspection commands.
- **Judge:** second independent adversarial verifier; verification-capable, may use a cheaper model.
- **Reflector:** independent introspector of the LOOP (not the test), runs at every terminal exit. Gets only
  the factual run trace; writes ONLY `.reflection.grow.jsonl`. **Advisory — MUST NOT edit any tool / spec /
  harness / product file** (self-modification guard). A proposal touching the reflection machinery itself
  carries `self_referential=true` for extra human scrutiny.

Semantic capability tiers, not provider model names. Run sequentially in one dedicated worktree; author,
skeptic, and judge must be distinct invocations; a reviewer must not inherit the orchestrator conversation
or another role's context.

## The additive constraint (the Grow↔Sharpen boundary)

The author may only ADD assertions/tests — append a `PROFILES`/`ENTRY_FACTORIES` row, a `parametrize`
case, an `@example`, or a new test function/file. It must NOT alter or remove an existing assertion; that
is Sharpen's job. The gate enforces this deterministically with `grow_gate.py additive` — a real adds-only
assert-node diff: every before-assert must still be present after, so any altered/removed node ⇒ MUTATIVE ⇒
bounce. **Do NOT use `sharpen_gate.anticheat_diff` for this** — it only flags a specificity *drop*, so a
same-tier value change (`== 1` → `== 2`) slips past as 'additive' (review C4). A mutative proposal routes to
Sharpen, recorded `left-unclosable: ["mutative — Sharpen scope"]`.

## Review payload and decision

The author receives the orphan-scenario coordinate, the target symbol, the applicable invariant family,
the existing target test file (to extend, not duplicate), and at most the previous objective-gate bounce.
The skeptic and judge each receive exactly:

1. factual proposal fields `{target_test, dimension, change}`;
2. the unified diff;
3. the target symbol name, solely to locate code;
4. the adversarial task from `PROMPTS.md`.

They do not receive rationale, claimed growth, authorities, or another review. The judge runs only after a
skeptic `UPHELD`. Final verdict is `UPHELD` iff both verdicts are `UPHELD`; every other result is
`REFUTED`. A refuting reviewer may return `better_fix` when its evidence preserves the objective but shows
the candidate is the wrong intervention; the adapter records it but never turns it into an UPHOLD or applies
it in the same run. Persist review provenance in the `review_provenance` shape in `contracts.json`; never
copy `skeptic_verdict` into `verdict` without applying the judge result.

## The gate is applicable-arm-driven

Unlike Sharpen's fixed two arms, Grow runs the arms **applicable to the gap's kind** (`grow_gate`
subcommands):
- **scenario/config gap** → `liveness` (always) + `context` with `--deselect <grown-test-node>` (so an
  extend-before-add edit's grown test is excluded from the OLD baseline, else the delta collapses to ∅) +
  `growth-adhoc` (an author-supplied one-line CUT mutant the test must kill / old suite must survive) —
  **arm-1 is non-optional for a scenario gap**: without it the gate proves only dead-config, not growth
  over covered code (review C2). `growth` (cosmic-ray) is used instead when the module is a `poe mutate`
  target.
- **concurrency gap** → `concurrency` INSTEAD of 1–3: a pair of PASSING tests (regression + a
  self-certifying negative control), plus arm-2 `liveness` run on the CONTROL to confirm its oracle is
  live (that is what gives the passing control teeth — review C6).

The harness selects arms from the gap kind and records which arms ran and which were `n/a` in
`axes_not_applied` (the guard against a silent no-run).

## Failure semantics

- No green baseline / target won't build: append `state: dry-run`, list quarantined nodes, stop.
- Grown test RED on pristine code: this is **outcome-class 2 (latent bug)**, not a gate failure — file the
  product issue, record `state: filed`, do NOT massage it green.
- No orphan scenario / nothing to grow: record the gap `left-unclosable` (or skip); never ask the author to
  fabricate a vacuous test.
- Objective gate bounced (vacuous / redundant / no new line / raceless): revert the edit; retry ≤ the cap
  with only the bounce carried forward; then record `left-unclosable` with the last bounce.
- Refuted candidate with `better_fix`: revert it, record the recommendation + scope, stop.
- Missing isolation or identity: append `state: dry-run`; never open a PR.
- Unverified open-PR exclusion: force `openPr=false` for the run.

## Adapters

`harness.js` is the Claude Workflow adapter; its inline schemas mirror `contracts.json` (that runtime has
no filesystem access). A Codex adapter (context-free subagents for the three judgment roles; deterministic
commands run directly) mirrors `.agents/skills/sharpen-loop/` when built.
