# Sharpen host-adapter contract

`SPEC.md` owns behavior. This file owns the transport boundary between that behavior and an agent host.
The deterministic instruments under `overlay/tools/` are the shared core; a host adapter supplies shell
execution, isolated agent invocations, phase reporting, and optional PR transport.

## Host operations

An adapter must provide these semantics:

| Operation | Contract |
|---|---|
| `exec(command, cwd)` | Run a deterministic command and return exit code plus complete output. |
| `invoke(role, prompt, schema, isolation)` | Start a fresh agent context, validate its result against `contracts.json`, and return both result and host invocation id. |
| `phase(name, detail)` | Report progress without changing repository state. |
| `record(record)` | Append one JSONL object using `sharpen_ledger.py` hashing and the ledger manifest version. |
| `open_pr(body)` | Optional; available only after explicit `openPr=true` and every ship guard passes. Never merge. |

The adapter, not an agent response, assigns invocation identity. Use the opaque id returned by the host.
If the host exposes no id, a unique adapter-assigned invocation label is acceptable only when the host
structurally guarantees that each call creates a fresh context. Otherwise fidelity is unproven and the
run is `dry-run`.

## Agent roles

- **Author:** generation-capable; may edit the single target test file.
- **Skeptic:** independent adversarial verifier; read-only except for inspection commands.
- **Judge:** second independent adversarial verifier; verification-capable and may use a cheaper model.

These are semantic capability tiers, not provider model names. An adapter may choose models, but shared
files must not name a provider-specific model.

Run the roles sequentially in one dedicated worktree. Author, skeptic, and judge must be distinct
invocations. A reviewer must not inherit the orchestrator conversation or another role's context.
Cross-family routing is recommended when the host roster offers it, per `CONTRIBUTING.md` §4; it is not
a replacement for isolation and its unavailability does not invalidate an otherwise faithful review.

## Review payload and decision

The author receives the survivor or actionable-lint coordinate, target test, applicable rubric, and at
most the previous objective-gate bounce. The skeptic and judge each receive exactly:

1. factual proposal fields `{target_test, axis, change}`;
2. the unified diff;
3. the touched production function name, solely to locate code;
4. the adversarial task from `PROMPTS.md`.

They do not receive rationale, claimed kills, authorities, or another review. The judge runs only after
a skeptic `UPHELD`. Final verdict is `UPHELD` iff both verdicts are `UPHELD`; every other result is
`REFUTED`. A refuting reviewer may return `better_fix` when its evidence preserves the objective but
shows that the candidate is the wrong intervention. The adapter records that recommendation but never
turns it into an UPHOLD or applies it in the same run.

Persist review provenance in this shape:

```json
{
  "author": "host invocation id",
  "skeptic": "host invocation id",
  "judge": "host invocation id or null",
  "skeptic_verdict": "UPHELD or REFUTED",
  "judge_verdict": "UPHELD, REFUTED, or null",
  "verdict": "final two-reviewer verdict"
}
```

Never copy `skeptic_verdict` into `verdict` without applying the judge result.

## Failure semantics

- No green baseline: append `state: dry-run`, list quarantined nodes, and stop.
- No complete mutation DB: continue only on a target-grounded actionable Conformance hit. Any assertion
  replacement/removal must pass `sharpen_gate.py preserve`; exact-byte restoration is mandatory.
- No actionable finding: record `left-undone`; do not ask the author to fabricate value.
- Objective gate exhausted: record `left-undone` with the last bounce.
- Refuted candidate with `better_fix`: revert it, record the recommendation and its scope, then stop;
  route `outside-sharpen` work for separate maintainer authorization.
- Missing isolation or identity: append `state: dry-run`; never open a PR.
- Unverified open-PR exclusion: force `openPr=false` for the run.
- On `openPr=true`, run `uv run poe all` after both reviews and before opening the ready PR. Failure
  forces `dry-run` and no PR.

## Skill handoffs

Missing mutation data is an out-of-band `test-adequacy` prerequisite; never launch a campaign inside a
Sharpen run. Complete survivors enter as coordinates. Source/tool/config/dependency changes route to
`contribute` as a separate PR, while ordinary test-only Sharpen work stays here. Repeated reflection
findings that need external prior art route to `research`. Optional host LSP navigation may locate exact
symbols/callers; never invoke or depend on the infrastructure-only `pyrefly-lsp` skill.

## Adapters

`harness.js` is the Claude Workflow adapter. Its inline schemas mirror `contracts.json` because that
runtime has no filesystem access. The repo-local `.agents/skills/sharpen-loop/SKILL.md` is the Codex
adapter: Codex runs deterministic commands directly and uses context-free subagents only for the three
judgment roles.
