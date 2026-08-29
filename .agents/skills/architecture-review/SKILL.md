---
name: architecture-review
description: >-
  Judge whether the existing architecture is fit for this product through an isolated, code-grounded
  reviewer and explicit quality axes. Use after a migration, before a feature that assumes the current
  shape, when the meters are green but something feels wrong, for “did we pick the right principles”,
  “is this over-engineered”, “what should we cut”, or on a standing cadence. Produces ranked findings,
  what to keep/delete, an argued principles verdict, and unverifiable claims. NOT for one change or PR
  (contribute), a collaborative multi-slice decision or re-enfolding (architecture-inquiry), a chosen
  conversion plan (plan-migration), or style, naming, and local bugs.
metadata:
  project: saitenka
---

# architecture-review

An architecture is only good *for* something. This review asks whether the design serves what the
product actually is — not whether it is pure, modern, or internally consistent, all of which a bad
architecture can be.

Run it on a cadence. A one-time check answers "is it good now", which is the least useful version of
the question; the value is in the delta between two runs and in catching the drift that no gate sees.
That delta needs somewhere to live: **`.agents/architecture-review/`** holds the run reports and the
claim census, and [its `SPEC.md`](../../architecture-review/SPEC.md) owns the artifact contract. This
file owns the judgement.

## Boundary versus architecture-inquiry

This skill judges the artifact as it exists. Its isolation rule is load-bearing: an independent reviewer
must derive the product and design rather than inherit the author's framing. Use `architecture-inquiry`
when the work instead requires collaborative analytical slices, verified prior art, constraint auditing,
re-enfolding, and a human choice among arrangements. Do not pass a preferred architecture from an inquiry
into this reviewer as context; that turns an independent gate into confirmation.

## 0. Isolate the reviewer

**Spawn a subagent with no conversation context.** This is the gate property, not a convenience: the
person who built the thing cannot see the assumption they built it on, and a reviewer handed the
rationale judges the story rather than the artifact.

What it must **not** get is your account of the design. What it must get:

- **the product, not your summary of it** — tell it to derive the goals itself from `README.md`, the
  `docs/` tree and the CLI surface. Those goals are the yardstick for every judgement;
- **the scope**, stated (below), and **the contract**: this file's §1–§5, `references/axes.md`,
  `references/evidence.md`;
- **the agenda** — the census's `argued` rows, as suspects (§3.1);
- **the environment constraints**, or it will fork-bomb the machine on its first search:
  `.agents/rules/searching.md` (the `grep`/`find`/`rg` shim ban) and `uv run` for everything Python.
  A subagent does not inherit your loaded rules. Restate them in the brief.

**Scope is a choice, not a default.** Whole-repo on a cadence, so drift is caught by schedule. A
*named module plus its collaborators* right after a migration lands — the depth buys findings in
seams a broad pass skims, at the cost of anything cross-cutting. State which in the report's first
line; a scoped review that reports like a whole-repo one overclaims by omission.

## 1. Establish declared vs enforced vs true

These come apart, and the gap is where the findings are.

| | means | how to check |
| --- | --- | --- |
| **declared** | a doc or a plan says it | read it, then distrust it |
| **enforced** | a gate fails when it breaks | `[tool.poe.tasks]` — is there a task, is it in `all` |
| **true** | the code does it | read the code |

Prose is not gated (`evidence.md`, "Declared, enforced, true"). Check whether any invariant page
carries a statement the code has contradicted — `c1624d20` fixed two in one file.

A test's existence does not move a claim to **enforced**. A test that has never been shown to fail
against the thing it guards is `declared`, in test form: `812ba04f` retired a negative control that
had stopped being able to fire, and every test of one render tier drove the path production had
already abandoned. **Ask what the test would have to see to fail.**

## 2. Work the axes

[`references/axes.md`](references/axes.md) carries them and what each is looking for. Weight by the
product; do not pad. The two that produce the most and are most often skipped:

- **Is each concept earning its keep?** Count what a newcomer must hold at once, and propose which
  could merge or be deleted with no loss. Vocabulary growth is the standing failure mode of a design
  that shipped.
- **Were these the right principles at all?** Argue the strongest case *against* the current model
  for this product, price the alternatives, and then say where you land. A review that never states
  the case against has not tested the design.

## 3. Measure, do not assert

The tools exist so this review is cheap; `poe arch-map` alone feeds four axes.

```sh
uv run poe arch-map          # imports + cycles classified, owner->features->events, commands, seams
```

Gate status alone says little about architecture fitness. Read the focused census for the mechanism
under review:

```sh
uv run poe port-probe-census       # the per-site table
uv run poe reducer-purity-census   # impure readings per reducer
```

Read what a meter *measures* before quoting it — `reducer-purity` is the narrow one, and says so in
its own output line ("Decision functions outside the route table are not measured here").
`arch-map` separates runtime import cycles from annotation-only ones; re-reporting the latter as
coupling sends someone to break up a package that was never coupled.

Performance claims cite `BENCHMARKS.md` or a profiler run, or carry the harness that produced them
(`evidence.md`, "Your own measurement is admissible; label it"). An unmeasured latency claim is
ranked as speculation or dropped.

## 3.1 Give the reviewer an agenda, take one back

A review with no agenda rediscovers the terrain by hand every run and finds whatever it trips over.
The **claim census** in `.agents/architecture-review/census.json` — each of a module's own statements
marked `gated` / `tested` / `argued`, by the classes in
[`references/claim-classes.md`](references/claim-classes.md) — is what points it.

- **In**: hand over the `argued` rows as **claims to attack**, never as findings to confirm. Whether
  an agenda narrows a reviewer is **open** — one run says no (its two worst findings were off-agenda,
  and one result was that a *census row was itself wrong*), and one run is one run. So check it every
  time, and the check is cheap: **if every finding sits on the agenda, the agenda replaced the
  review** — discard that run and re-run blind before acting on it.
- **Out**: write the report's "could not verify" rows back as `argued` census rows with their
  remedies, per the loop's `SPEC.md`. This is the step that makes a cadence mean anything.

Isolation is unchanged. A reviewer handed the answer judges the story — the failure §0 exists to
prevent. Handed a list of suspects, it does the opposite.

## 4. Hold the standard of evidence

[`references/evidence.md`](references/evidence.md) is the contract, and it is not optional reading:
the reviewer is bound by all of it, not by a summary here. The one rule to carry into every sentence
you write — **open the artifact that would falsify a claim before making it.** A matching pair of
counts, or two things sharing a call shape, is a correlation, not a mechanism.

## 5. Report

Write it to `.agents/architecture-review/runs/YYYY-MM-DD-<scope>.md` as delivered — never compress a
finding or drop its discriminator into a summary.

Scope line · verdict (a few sentences) · findings ranked P0→P3, each with axis, failure scenario,
discriminator, **age**, remedy · **what is genuinely good**, specifically — an all-negative review is
a failed review and gets ignored · what you would cut, with the loss · the principles answer · what
you could not verify.

**Rank by what a false claim costs a user of this product**, not by how wrong it is. P0: it can drop
frames, hang, crash, corrupt a deck, or put a model-invented reading on a card. P1: it degrades
badly — an optional dependency that crashes instead of warning, a lifecycle that leaks. P2: it
misleads the next maintainer — a meter or doc the code contradicts. P3: everything else. A clean
module may honestly produce no P0 or P1; say so rather than promoting a P2.

**Age** is one clause per finding: is this new, or has it always been true? `git log -S` on the
line, or the commit that introduced the caller. It changes the rank (a regression from the work
under review is live, and someone is running it) and the remedy — in run 1, both fixed findings were
days old and from the migration being reviewed, which made the real finding *that migration's tests
never drove the path it shipped*.

Findings that survive become issues or a plan; findings about a *conversion* hand off to
**plan-migration**, and a single change driven to a PR hands off to **contribute**.

When several scoped findings imply a new whole-system direction, hand them to **architecture-inquiry**.
A scoped review may establish local facts; it does not license a whole-system recommendation until those
facts are re-enfolded with the other operational slices and audited for unasked constraints.

## Verify

`bash .agents/skills/architecture-review/scripts/smoke.sh` and
`bash .agents/architecture-review/scripts/smoke.sh` (both grep-free — safe to run here).
