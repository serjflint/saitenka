# Standard of evidence

An architecture finding is acted on. That asymmetry is the whole reason this file exists: a wrong
census can be re-run, but a wrong architectural diagnosis lands in a rule file, a plan, or a rewrite,
and it outlives the reasoning that produced it. So the bar is higher here than for a code review,
and confidence is not the bar.

## Falsify before you claim

**Open the artifact that would falsify a claim before making it** — the signature, the AST
classification, the API field, the meter's own definition.

A matching pair of counts and a shared call shape are exactly the evidence that *feels* like a
mechanism. A clean correlation is more suspicious, not less: the cleaner it is, the more likely it is
an artifact of how you counted.

Three diagnoses from one past review, each stated confidently, each producing a specific
recommendation that would have been acted on:

| claimed | inferred from | actually | what settled it |
| --- | --- | --- | --- |
| a documentation convention was growing the host | two totals matching | the thing blamed had *fallen*; the growth was a different kind entirely | classifying the changed members from source, rather than counting |
| two competing runtimes, one unmigrated | one is called synchronously, the other through a mailbox | two *layers* that compose deliberately; the classification was already correct | reading the entry-point signatures |
| review coverage explained a delivery difference | merged versus unmerged | neither side had ever been reviewed | one `gh pr view --json reviews` query |

The shape is identical each time: a surface correlation reported as a cause, without opening the
thing it was about.

## Reproduce one cause at a time

A repro that fires is not yet a mechanism. Shrink it until exactly one cause remains, because the
mechanism is what the remedy gets built from and a confounded repro hands over the wrong one.

Observed (`a2b8f1c4`): a real finding — a frame flag that stayed set forever, re-uploading the
tooltip every poll tick — was reported as caused by an unfiltered band enumeration. It had **two**
independent causes, and the one named was the minor one; the dominant cause was an overscan
mismatch, visible only once the other was held fixed. Both fixes were needed. A remedy built from the
reported mechanism alone would have shipped, tested green, and left the bug.

## Your own measurement is admissible; label it

`BENCHMARKS.md` or a profiler run is the standard, but for a claim nobody has measured, a harness you
write is often the only way to settle it. That is fine, with two conditions.

**Name the harness** — what it stands in for, and what it cannot see. A number from a fake transport
is a number about the fake transport.

**Keep it out of the verdict until it reaches a production path.** A per-tick cost measured against a
stand-in belongs in the finding with its harness named, and in "could not verify" with the live run
that would confirm it. Stated bare in a verdict, it reads as measured behaviour of the shipped app,
and that is the sentence that gets quoted onward.

## State the discriminator

Every finding names the check that separates it from its most plausible innocent explanation, so a
reader can verify it without redoing the work. "Delegators fell while substantive methods grew" is a
discriminator. "The host got bigger" is not.

This also makes a finding falsifiable later. A finding nobody can re-check becomes folklore.

## Declared, enforced, true

Never collapse these. Prose is not gated: `poe docs-refs` / `docs-consts` pass on a page whose claims
the code contradicted months ago, because they check references and constants, not claims —
`c1624d20` fixed two such claims on one invariants page.

When quoting a meter, read what it *measures* first. A meter named for a concept usually covers a
subset of it (`7cee5043` — `reducer-purity` now says so in its own output), and the summary line, not
the docstring, is what gets quoted into the next document.

## Rank speculation as speculation

Speculation labelled as speculation is useful; it tells the next run where to look. Speculation
labelled as a finding poisons the review — once one entry is found to be unchecked, the rest are
discounted whether or not they deserve it.

If a claim needs a live session, a profiler, or hardware you do not have, say so and rank it lower.

## The "could not verify" section is mandatory

A table: the claim, why it could not be settled, and **what would settle it** — the specific command,
run, or measurement.

Two reasons it is not optional. It bounds the review, so a reader knows which parts are load-bearing.
And its rows are the next run's agenda, which is what turns a one-time check into a practice.

## An all-negative review is a failed review

Name what is genuinely good, specifically, with the evidence. Not for balance — a review that reads
as an indictment gets filed and ignored, and the parts worth defending are exactly the parts a later
refactor will remove by accident because nobody wrote down why they were there.
