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

## Review payload and decision

The author receives the survivor or actionable-lint coordinate, target test, applicable rubric, and at
most the previous objective-gate bounce. The skeptic and judge each receive exactly:

1. factual proposal fields `{target_test, axis, change}`;
2. the unified diff;
3. the touched production function name, solely to locate code;
4. the adversarial task from `PROMPTS.md`.

They do not receive rationale, claimed kills, authorities, or another review. The judge runs only after
a skeptic `UPHELD`. Final verdict is `UPHELD` iff both verdicts are `UPHELD`; every other result is
`REFUTED`.

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
- No complete mutation DB: defer Efficacy explicitly; continue only on a real actionable Conformance hit.
- No actionable finding: record `left-undone`; do not ask the author to fabricate value.
- Objective gate exhausted: record `left-undone` with the last bounce.
- Missing isolation or identity: append `state: dry-run`; never open a PR.
- Unverified open-PR exclusion: force `openPr=false` for the run.

## Adapters

`harness.js` is the Claude Workflow adapter. Its inline schemas mirror `contracts.json` because that
runtime has no filesystem access. The repo-local `.agents/skills/sharpen-loop/SKILL.md` is the Codex
adapter: Codex runs deterministic commands directly and uses context-free subagents only for the three
judgment roles.
