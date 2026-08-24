# Architecture inquiry contract

Use this reference for each analytical slice and for the re-enfolded decision dossier. It is intentionally
mechanism-oriented: named patterns are inputs, not answers.

The re-enfolding move is **Bohm-inspired**, not a claim that this procedure is Bohm's method. Bohm's account
of fragmentation and the whole informs the return from parts to a reconstructed whole
([David Bohm Society, “Postmodern Science and a Postmodern World”](https://www.davidbohmsociety.org/library/postmodern/));
the suspension of assumptions and resistance to prematurely forcing consensus draws on
[*On Dialogue*](https://www.davidbohmsociety.org/library/on-dialogue/). Analytical slices, atomic ledgers,
constraint audits, pilots, and retirement meters are Saitenka-specific engineering adaptations.

## Universal slice output

1. **Provisional synthesis** — what assumption changed, unresolved tensions, no recommendation.
2. **Atomic claims ledger** — source/revision, proven subclaim, non-proven inference, status, confidence.
3. **Grounded scenario traces** — admission through visible settlement, including failure and retirement.
4. **State/fact ownership** — authoritative fact, projections/tokens, lifetime, writers, readers.
5. **Mechanism contracts** — guarantee supplied, guarantee absent, interaction failure prevented or created.
6. **Strongest case for the present shape** — steelman it before calling anything smell.
7. **Missing evidence and policy questions** — what research cannot decide and what would settle the rest.

## Conditional lenses

Apply a lens only when it can falsify a candidate arrangement or settle a high-impact claim. Record why a
plausible lens was omitted; do not manufacture a section merely to complete a template.

- **Identity/cancellation or commit model** — when work can arrive late or a write can be ambiguous.
- **Composite arrangements** — the current shape plus materially distinct alternatives; include full traces,
  costs, lost guarantees, and strongest dissent. Do not target a fixed number.
- **Prior-art fragments** — when external mechanisms could supply a missing guarantee; exact revision and
  local mismatch, with shipping treated as existence only.
- **Ports/assembly or boundary comparison** — when freshness, lifetime capture, causal readability, or
  substitution differs across arrangements.
- **Bespoke/framework comparison** — when a dependency might supply a distinct missing capability.
- **Local discriminators** — when competing hypotheses remain; state hypothesis pair, invariant, isolated
  change, observable, and false positive.

A remote write may need write provenance while a hover path needs generation identities; another slice may
need neither. Preserve the evidence discipline, not this session's vocabulary or mechanism census.

## Constraint audit

For every cross-cutting “must”, record:

| Field | Question |
| --- | --- |
| Origin | User/product, existing code, prior art, reviewer, or convenience? |
| Class | Invariant, principle, mechanism, hypothesis, or product policy? |
| Invariant protected | Which accepted observable promise does this principle serve? |
| Scope | Whole product or only this slice/scenario? |
| Failure if removed | Concrete user-visible or maintenance failure? |
| Enforcement | Declared, tested, gated, or merely true today? |
| Human acceptance | Who accepted this principle and its cost as an architectural constraint? |
| Retirement | What evidence would allow the constraint to be weakened or deleted? |

Reject constraints whose only rationale is that a framework supplies them. Do not silently upgrade a local
mechanism into a product invariant. Only accepted product invariants are global by default; a principle with
an empty invariant, provenance, scope, removal failure, or human-acceptance field remains a proposal.

## Re-enfolding questions

- What does the whole now look like that no individual slice could show?
- Which slice conclusions conflict when composed?
- Which mechanisms serve three or more slices and may be infrastructure?
- Which similar call shapes hide different guarantees and must stay specialized?
- Where does one executor or composition root get mistaken for one policy owner?
- Which ownership statement is too broad to constrain new work?
- Which proposed abstraction merely relocates complexity or renames the existing script?
- Which open items are evidence gaps, and which are product decisions?
- What is the strongest coherent architecture that leaves the present composition unchanged?

## Decision dossier

```markdown
# <decision title>

## Decision question
One falsifiable boundary question, including what is out of scope.

## Product invariants
Observable promises; distinguish existing promises from proposed ones.

## Re-enfolded whole
The revised system account, changed assumptions, cross-slice tensions.

## Viable arrangements
For each: owners, mechanisms, guarantees, losses, concept tax, retirement path, strongest dissent.

## Unnecessary constraints rejected
Requirements introduced by tooling, analogy, or local optimization rather than the product.

## Evidence and discriminators
Confirmed facts, missing facts, and the smallest local experiments that separate options.

## Human decisions
Only policy choices whose answer changes the architecture.

## Bounded pilot
Scope, authority/writer model, ordering or consistency failure semantics, scenario traces, exact-tree review,
and stop conditions. Include a one-writer proof only for an accepted one-writer invariant, and a
policy-retirement meter only when the pilot retires or replaces policy.
```

## Saturation test

Research is saturated when all are true:

- another round has no named missing category to search;
- each high-impact claim is confirmed, rejected, or paired with a concrete local discriminator;
- new prior art only repeats known mechanisms;
- unresolved items are explicitly product policy or experiments;
- the strongest case for the current shape and at least one materially different composition are both intact.

Saturation means “ready to re-enfold”, not “ready to recommend”.
