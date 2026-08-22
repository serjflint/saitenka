---
name: architecture-review
description: >-
  Judge whether the architecture is sound, efficient, and the right one for what this product is —
  a fitness review run by an isolated reviewer against ten axes, with every claim checked against the
  code rather than against a document's claim about the code. Use when a large migration or refactor
  lands, before committing to a feature that assumes the current shape, when the meters read green
  but something feels wrong, when asking "did we pick the right principles", "is this over-engineered",
  "what should we cut", or on a standing cadence so drift is caught by schedule instead of by
  accident. Produces ranked findings, what to keep, what to delete, an argued answer to the
  principles question, and an explicit list of what could not be verified. NOT for reviewing one
  change or a PR (use contribute); NOT for planning a conversion (use plan-migration); NOT for style,
  naming, or local bugs — those are code review and this skill will drown in them.
metadata:
  project: saitenka
---

# architecture-review

An architecture is only good *for* something. This review asks whether the design serves what the
product actually is — not whether it is pure, modern, or internally consistent, all of which a bad
architecture can be.

Run it on a cadence. A one-time check answers "is it good now", which is the least useful version of
the question; the value is in the delta between two runs and in catching the drift that no gate sees.

## 0. Isolate the reviewer

**Spawn a subagent with no conversation context.** This is the gate property, not a convenience: the
person who built the thing cannot see the assumption they built it on, and a reviewer handed the
rationale judges the story rather than the artifact. Hand it the repo and the axes; nothing else.

Give it the product, not your summary of the design — tell it to derive the goals itself from
`README.md`, the docs site and the CLI. Those goals are the yardstick for every judgement.

**Scope is a choice, not a default.** Whole-repo on a cadence, so drift is caught by schedule. A
*named module plus its collaborators* right after a migration lands — the depth buys findings in
seams a broad pass skims, at the cost of anything cross-cutting. Say which one you are running; a
scoped review that reports like a whole-repo one overclaims by omission.

## 1. Establish declared vs enforced vs true

These come apart, and the gap is where the findings are.

| | means | how to check |
| --- | --- | --- |
| **declared** | a doc or a plan says it | read it, then distrust it |
| **enforced** | a gate fails when it breaks | `[tool.poe.tasks]` — is there a task, is it in `all` |
| **true** | the code does it | read the code |

Prose is not gated here. `poe docs-refs` / `docs-consts` check references and constants, never
claims — so an invariant page can carry a statement the code has contradicted for months and stay
green. Assume at least one does; the last run found two in one file.

A test's existence does not move a claim to **enforced**. A test that has never been shown to fail
against the thing it guards is `declared`, in test form — two written here passed against their own
target regression, and a whole tier's tests drove the code path production had stopped taking.
Ask what the test would have to see to fail.

## 2. Work the axes

[`references/axes.md`](references/axes.md) carries the ten and what each is looking for. Weight them
by the product; do not pad. The two that produce the most and are most often skipped:

- **Is each concept earning its keep?** Count what a newcomer must hold at once, and propose which
  could merge or be deleted with no loss. Vocabulary growth is the standing failure mode of a design
  that shipped.
- **Were these the right principles at all?** Argue the strongest case *against* the current model
  for this product, price the alternatives, and then say where you land. A review that never states
  the case against has not tested the design.

## 3. Measure, do not assert

The tools exist so this review is cheap; `poe arch-map` alone answers four axes.

    poe arch-map          # imports + cycles classified, owner->features->events, commands, seams
    poe cluster-map       # what a module touches on the host, by fact; --member NAME for the sites
    poe runtime-status · host-arity · host-mass · port-probe · reducer-purity

Read what a meter *measures* before quoting it — at least one here is scoped narrower than its name.
`arch-map` already separates runtime import cycles from annotation-only ones; re-reporting the
latter as coupling sends someone to break up a package that was never coupled.

Performance claims cite `BENCHMARKS.md` or a profiler run. An unmeasured latency claim is ranked as
speculation or dropped.

## 3b. Give the reviewer an agenda, take one back

A review with no agenda rediscovers the terrain by hand every run and finds whatever it trips over.
A **claim census** — the module's own statements about itself, each marked `gated` / `tested` /
`argued`, the classes in [`references/claim-classes.md`](references/claim-classes.md) — is what
points it.

- **In**: hand over the `argued` rows as **claims to attack**, never as findings to confirm. Priming
  does not tunnel an isolated reviewer: the last run's two worst findings were off-agenda, and one
  of its results was that a *census row was itself wrong*.
- **Out**: every "could not verify" row becomes a census row with its remedy, so it survives between
  runs instead of being re-derived.

Isolation is unchanged. A reviewer handed the answer judges the story — the failure §0 exists to
prevent. Handed a list of suspects, it does the opposite.

## 4. Hold the standard of evidence

[`references/evidence.md`](references/evidence.md) is the full contract. The three rules that carry it:

- **Open the artifact that would falsify a claim before making it.** A matching pair of counts, or
  two things sharing a call shape, is a correlation — not a mechanism. A clean correlation is more
  suspicious, not less.
- **State the discriminator** in the finding, so a reader can check it without redoing the work.
- **A "what I could not verify" section is mandatory.** A review without one is overclaiming, and
  the entries are the next run's agenda.

## 5. Report

Verdict (a few sentences) · findings ranked P0→P3, each with axis, failure scenario, discriminator,
**age**, remedy · **what is genuinely good**, specifically — an all-negative review is a failed
review and gets ignored · what you would cut, with the loss · the principles answer · what you could
not verify.

**Age** is one clause per finding: is this new, or has it always been true? `git log -S` on the
line, or the commit that introduced the caller. It changes the rank (a regression from the work
under review is live, and someone is running it), and it changes the remedy — two P0/P1 findings
from the last run were days old, both from the migration being reviewed, which made the real finding
*that migration's tests never drove the path it shipped*.

Findings that survive become issues or a plan; findings about a *conversion* hand off to
**plan-migration**, and a single change driven to a PR hands off to **contribute**.

## Verify

`bash scripts/smoke.sh` (grep-free — safe to run here).
