---
name: plan-migration
description: >-
  Price an already-chosen migration before it starts: enumerate the class, census and price the unit,
  choose and reject leverage devices explicitly, declare retirement beside debt, then batch the family and
  codemod the mechanical part. Use for a migration/refactor plan, converting every X to Y, retiring a god
  object or host parameter, branching by abstraction, deprecating an API across many sites, “how long will
  this refactor take”, or “facade, port, or codemod”. NOT for a single-site fix, one PR (contribute), choosing
  ownership or architecture across operational slices (architecture-inquiry), deciding where a convention
  lives (agents-md), or writing a test (write-test).
metadata:
  project: saitenka
---

# plan-migration

A migration is priced in *decisions per site*, and the price is knowable on day 1 from a census that
usually already exists. The failure this skill exists to prevent is not a wrong estimate — it is
never computing one, starting, and discovering the shape only after most of the budget is spent.

The architecture must already be chosen. If the open question is where policy or state should live, return
to `architecture-inquiry`; this skill begins when a multi-site conversion has a decided source and target.

Work the four moves in order and write each answer into the plan. A plan missing move 2's rejected
alternatives is a plan that never considered one.

## 0. Enumerate the class before converting the first instance

A fix described as a class must be applied as one. Argue the general case in a commit subject and
convert only what is in front of you and you have written the class down without applying it — which
is exactly how a large family gets converted one member at a time while a document explaining the
batching sits unread in the same branch.

So: enumerate the family first, then convert it. This applies to the work that is *running*, not
only to the bug in front of you.

## 1. Census authority, then price the unit

    sites × the kind of decision each site needs × the observed rows-per-commit rate = commits

Read mechanical counts from the repository census tools rather than copying them into the plan.

For a responsibility migration, also use the architecture-inquiry skill's canonical
[`authority-reachability.md`](../architecture-inquiry/references/authority-reachability.md). Price decisions
per authority path, not every textual mention.

If the result exceeds the expected shape by an order of magnitude, that is the signal to **stop and
look for a shared shape** — not a reason to start early. Sites are rarely independent: several names
over one owner are one port, and `poe cluster-map` is what says so.

## 2. Choose a leverage device explicitly, and record the rejection

Facade · shared port · codemod — [`references/leverage-devices.md`](references/leverage-devices.md)
carries what each costs and when it pays. **A ratchet is not one of them**: it is a safety device
that makes the debt visible, and it does not make any site cheaper.

Two traps, both observed here:

- A correct rejection of one device read later as a rejection of *all* of them. "Ratcheted rather
  than run in parallel" is a decision about a shadow runtime, not a decision that no intermediate
  abstraction should exist — and nothing evaluated a facade for months afterwards.
- A "would a port help?" column evaluated on **one** cluster and applied **globally**.

Name the device you chose and the ones you rejected, each with its reason, in the plan.

## 3. Declare a retirement meter next to the debt meter

A migration whose success condition is a *retirement* needs a meter that distinguishes "the new thing
arrived" from "the old thing left". A debt count falling while the retired object keeps growing is
the failure mode, and only two numbers side by side can see it. `poe host-mass` is the standing
instance — declare yours on day 1, not after the first surprise.

**Responsibility migrations need an authority/writer retirement meter.** Moving fields, adding a
collaborator, reducing LOC, or turning methods into delegators does not prove that ownership moved. When the
migration also retires policy, meter the substantive admission, completion, fallback, publication, and
lifecycle decisions. Retirement means the claimed authority terminates at the bounded owner; the old host
may remain as composition root or sole effect executor. Add a one-writer proof only when the accepted
invariant requires one writer. Otherwise name the multiple-writer ordering or consistency mechanism and its
failure semantics.

Self-attack the intended boundary using that same canonical lens. Keep the guard proportionate to the
retired semantic path; do not freeze private layout or benign test construction.

## 4. Batch the class; codemod the mechanical part

Convert a whole family in one commit rather than one member per commit, and drive the mechanical
edit from a codemod — [`references/codemod-recipe.md`](references/codemod-recipe.md) has the runnable
handoff (`tools/cluster_map.py --member` → LibCST, under `uv run --group codemod`). Batching the
family and codemodding its mechanical part retired debt several times faster per commit here, and by
a wider margin per hour, than converting one function per commit did.

The residue after a codemod is the real work: the sites the transform declined to touch are the ones
that needed a decision, and that list is the plan's next section.

## Verify

`bash scripts/smoke.sh` (grep-free — safe to run here).
