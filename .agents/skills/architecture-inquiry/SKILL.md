---
name: architecture-inquiry
description: >-
  Prepare a grounded architecture decision across interacting operational slices — a Bohm-inspired
  whole-to-parts-to-whole inquiry for god objects, ownership boundaries, architecture smell,
  event/reducer/controller choices, and decision dossiers. Starts from product invariants, withholds local
  recommendations, treats prior art as mechanism primitives, audits unasked constraints, and re-enfolds
  before a human choice and bounded pilot. Use for “where should responsibility live”, “is this god object
  one responsibility”, “should we use a broker/pipeline/DSL”, or “prepare the architecture decision”. NOT
  for a periodic isolated fitness gate (architecture-review), external research alone (research), an
  already-chosen multi-site conversion (plan-migration), or one PR (contribute).
metadata:
  project: saitenka
---

# architecture-inquiry

Use this when the architecture question is wider than one finding but narrower than “redesign the app”. The
procedure separates the whole into operational slices, then actively recombines them before any decision.
The separation finds mechanisms; the re-enfolding prevents a locally tidy answer from becoming a globally
wrong one.

The full slice contract, constraint audit, and dossier template are in
[`references/inquiry-contract.md`](references/inquiry-contract.md).

## Boundary versus neighboring skills

- `architecture-review` is an **isolated artifact-first fitness gate**. Its reviewer must not receive the
  design story. Use it on a cadence or after a migration.
- This skill is a **collaborative decision inquiry**. It may use several review and research rounds, but it
  withholds a decision until the slices are re-enfolded.
- `research` discovers and verifies external mechanisms. It does not choose Saitenka's architecture.
- After a human decision, `plan-migration` prices a multi-site conversion; a single bounded change can hand
  directly to `contribute`.

## 0. Freeze the question, not a solution

State the product boundary, the architecture smell being tested, and the user-visible invariants at risk.
Record disputed words such as “session”, “controller”, “feature”, “pipeline”, or “owner”; a label is not a
boundary until it says which future changes do **not** belong there.

Classify every proposed constraint as one of:

1. product invariant — observable promise worth preserving;
2. architecture principle — reusable rule chosen to protect an invariant;
3. mechanism — one implementation that supplies a named guarantee;
4. hypothesis — requires a discriminator or measurement;
5. product-policy question — research cannot decide it.

Only an accepted product invariant constrains every slice by default. An architecture principle must name
the invariant it protects, its provenance and scope, the failure caused by removing it, and the human who
accepted that trade. Keep the inquiry read-only until the human chooses a direction; do not turn findings
into fixes while the model is still being tested.

## 1. Start with the whole

Read the product surface, `ARCHITECTURE.md`, current decision notes, architecture-review reports/census, and
the code paths that would falsify the declared ownership. Establish **declared / enforced / true** separately.

Write a provisional whole with unresolved tensions and **no recommendation**. Size, line count, file count,
framework absence, mechanism count, and co-change during a migration are inspection signals, never proof.
Alongside control/data flow, trace **authority reachability** where ownership is disputed: which objects can
reach the mutable fact or substantive policy, through which seam, in each supported execution placement.
A layered call graph does not prove ownership when each layer can still forward the old authority.
Use the proportionate method in
[`references/authority-reachability.md`](references/authority-reachability.md); existing architecture meters
are mechanical evidence, not a complete authority proof.

## 2. Choose bounded operational slices

Slice by a user-visible operation with admission, authoritative facts, lifecycle, failure modes, and apply
semantics—not by whichever module is large. Pick the smallest set whose tensions cover the whole question.
Examples include one volatile interaction, one external commit, one session replacement, or one close path.

For each slice, use the universal outputs in `references/inquiry-contract.md`, then only the conditional lenses
that can change the decision. Trace grounded scenarios end to end; name writers and readers; separate supplied
from absent guarantees; state the strongest case for the current shape; list missing evidence. Do **not**
recommend from the slice or invent filler to satisfy an irrelevant lens.

## 3. Use prior art as primitives

External systems are evidence of mechanisms, not architectures to transplant. For every surviving fragment,
record its exact revision, accountable owner, scheduling/identity mechanism, guarantee supplied, guarantee
absent, strongest dissent, and exact mismatch with this product. Shipping proves existence only.

Use `research` when discovery is needed, but retain this contract even if that skill is unavailable. Verify
primary sources and local clones; a report proposes, source establishes.

## 4. Stop when research saturates

A new round is justified only by a named missing evidence class, unverified high-impact claim, new peer, or
new mechanism. Stop when a round adds no verified mechanism or gap and the residue is product policy or a
local experiment. Repeating the same categories with more prose is not corroboration.

## 5. Re-enfold the whole

Re-enfolding is not a summary of slice conclusions. Reconstruct the whole model and ask:

- Which initial assumptions changed?
- Which mechanisms are load-bearing across slices, and which were local conveniences?
- Where do slices demand incompatible ownership, ordering, latency, or durability?
- Which “requirements” were introduced by an attractive pattern rather than requested by the product?
- Does one execution authority conceal several independent policy owners?
- Would a responsibility label actually reject any future feature, or can it absorb everything?

Produce a revised whole, preserve unresolved product-policy questions, and explicitly remove unasked
constraints. A successful local pattern remains evidence, not a universal template.

## 6. Adversarially review the dossier

Give an isolated reviewer the evidence and candidate arrangements, but not the preferred answer. Ask it to
attack premises, find smuggled requirements, identify missing arrangements, distinguish mechanism from
correlation, and make the strongest case for doing nothing. If it only restates the dossier, the review failed.

The no-context reviewer does not inherit this agent's loaded repository rules. Its brief must include the
scope and dossier contract, `.agents/rules/searching.md`, the ban on shell `grep`/`find`/`rg`/`ag`/`pgrep`,
the permitted navigation surfaces (`git diff`, `git ls-files`, known-file reads, LSP, and repowise), and
`uv run` for Python. Do not trade architectural independence for an unsafe or ungrounded environment.

Address factual findings before the decision. Preserve legitimate dissent instead of converting it into a
fake consensus.

## 7. Hand the human a decision

The dossier names the question, product invariants, re-enfolded whole, viable arrangements, guarantees and
losses, unnecessary constraints rejected, strongest dissent, evidence gaps, and bounded discriminators.
Recommend only where evidence settles a technical choice; stop for human direction where product policy
changes the result.

## 8. Pilot, then re-enfold again

After the choice, hand a multi-site conversion to `plan-migration`; hand a bounded pilot directly to
`contribute`; or, when the inquiry must compose several quality primitives into one package-level proof,
return the invariant and discriminator to `assurance-pipeline`. Require:

- an explicit authority/writer model and exact scenario traces; name the ordering or consistency mechanism
  and its failure semantics wherever more than one writer participates;
- a reachability census proportionate to the claimed boundary, including non-production callers only when
  they can preserve a second write or policy path;
- a one-writer proof only for facts whose accepted invariant requires one writer;
- a policy-retirement meter when the pilot retires or replaces policy, not merely fields, delegators, or
  lines;
- preservation of owner-thread and lifecycle constraints only when the audit established and the human
  accepted the product invariant they protect;
- exact-tree adversarial review.

Re-enfold the post-pilot whole before selecting another slice. One successful extraction supports the model;
it does not prove every feature should have the same class shape.

## Verify

`bash scripts/smoke.sh` (grep-free).
