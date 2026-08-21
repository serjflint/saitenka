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
recommendation that would have caused damage:

| claimed | inferred from | actually | what settled it |
| --- | --- | --- | --- |
| a documentation convention was growing the host | two totals matching | the thing blamed had *fallen*; the growth was a different kind entirely | classifying every member by kind, rather than counting |
| two competing runtimes, one unmigrated | one is called synchronously, the other through a mailbox | two *layers* that compose deliberately; the classification was already correct | reading the entry-point signatures |
| review coverage explained a delivery difference | merged versus unmerged | neither side had ever been reviewed | one API query |

The shape is identical each time: a surface correlation reported as a cause, without opening the
thing it was about.

## State the discriminator

Every finding names the check that separates it from its most plausible innocent explanation, so a
reader can verify it without redoing the work. "Delegators fell while substantive methods grew" is a
discriminator. "The host got bigger" is not.

This also makes a finding falsifiable later. A finding nobody can re-check becomes folklore.

## Declared, enforced, true

Never collapse these. Prose is not gated: reference and constant checks pass on a page whose claims
the code contradicted months ago, because they check references and constants, not claims.

When quoting a meter, read what it *measures* first. A meter named for a concept usually covers a
subset of it — and the summary line, not the docstring, is what gets quoted into the next document.

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
