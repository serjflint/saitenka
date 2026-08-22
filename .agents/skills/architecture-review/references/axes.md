# The ten axes

Weight by the product. Each entry says what the axis is looking for and the shape of a real finding
on it — not a checklist to complete, and a review that scores every axis equally has judged nothing.

## 1. Fitness for purpose

Does the design serve *this* product? Name any machinery whose cost is paid without a matching goal.

The recurring error is importing a distributed-systems pattern into a single-process, single-user,
single-session tool. Deterministic replay, consensus, partition tolerance, multi-tenancy, an event
log with a second consumer — each is a real payoff somewhere, and none of them is a payoff where the
input is one local process you already have a fake for.

## 2. Soundness of the invariants

For each declared invariant, three separate questions:

- **is it held** in the code;
- **is it enforced** by something that fails;
- **is it worth holding** — what bug does it prevent, and does it cost less than that bug?

All three produce findings. An invariant that is true but costs more than the bug it prevents is a
finding. So is one declared and half-enforced, because the half nobody checks is where it rots. So
is a bound that is *worse than no bound* — an unbounded queue degrades and recovers, a bound that
tears the session down does not.

## 3. Efficiency on the hot path

Identify the path that must feel instant, then ask what the architecture added to it: queue hops,
thread transitions, allocations, a mailbox in front of a synchronous decision. Then ask the reverse
— what blocks the loop that should be off it.

The teeth are in the second question. A synchronous call on the thread that drains events turns any
slow dependency into a stall of the whole session, and the trigger is usually an optional
integration nobody thinks of as being on the hot path.

Measure or cite a measurement. This axis is where speculation is most tempting and least useful.

## 4. Coupling and cohesion

Package edges, real cycles, god objects. Ask whether module boundaries follow the *domain* or the
migration's history — a reducer sitting in the wrong package because that is where it was born is a
small finding with a large half-life.

Classify before counting: an annotation-only import cycle costs nothing under
`from __future__ import annotations`, and reporting it as coupling is a false alarm that discredits
the rest of the review.

## 5. Extensibility

Walk a concrete new feature through, twice — one with state, one without. Where must the host be
edited? What must be registered, and where is the table?

Asymmetry between two kinds of feature is the finding: if one plugs in by naming itself in a dict
and the other can only be added as a method on the host, the host absorbs the difference forever.

## 6. Simplicity vs accidental complexity

Count the concepts a newcomer must hold simultaneously. Then, for each, name what breaks if it is
merged into its neighbour. The ones with no answer are the cut list.

Vocabulary growth is the standing failure mode of a design that shipped: a payload-free effect
spelled in seven places, near-identical stores differing only by which construction path made them,
two unions with overlapping members. None of these is a bug; together they are the reason nobody can
hold the model.

## 7. Failure modes and resilience

Disconnect and reconnect, teardown ordering, stale results arriving after the thing they belonged
to, unbounded queues, absent optional dependencies. Where does the design degrade gracefully, and
where does a recoverable condition escalate into an unrecoverable one?

## 8. Testability

Does the architecture make the *right* things cheap to test — and is the suite shaped by the design
or fighting it? A fake that has drifted from production, or a test seam that sets up state the
runtime cannot reach through the path production uses, is an architecture finding rather than a test
finding: it means the seam is in the wrong place.

## 9. Evolvability

Name the two changes that would be most expensive today, and say whether the architecture caused
that or merely failed to help. Second backend, second language, plugin API, swapping a renderer.

## 10. Were these the right principles at all

Spend the most here; a review that skips it has checked the execution and not the design.

Argue the strongest case **against** the current model for this product. Then price the alternatives
concretely — what each would have cost, what it would have bought, and what it would have made
impossible. Then land somewhere, in terms of the product's goals rather than of purity.

Split the answer. "The principles are right but the sizing is not" is a common and useful verdict:
some invariants earn their cost several times over while others were adopted as a set. Say which are
load-bearing and would be kept at any price, and which are ceremony that arrived with them.
