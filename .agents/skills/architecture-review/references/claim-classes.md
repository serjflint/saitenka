# Claim classes

"Verify the claims" is unactionable. These nine shapes are not — each needs a *different* check, and
each is here because it was found true of this repo, quoted as authority, in one session.

The unit of verification is never the sentence. Every falsehood below was **locally true**:

> `_render_band`: "Rasterise row `i`'s band WITHOUT touching shared state — safe on a worker thread.
> **The caller stores the result under the lock.**"

Accurate about the function. Every caller violated it, for months, and the docstring's own precision
is what made it invisible — a reader checking the function agrees and moves on. The unit is **the
sentence plus the thing it constrains**, which is usually somewhere else.

| # | class | what it looks like | how it is caught |
| --- | --- | --- | --- |
| 1 | **Scope lie** | true of the unit, false of its callers | an AST rule over the call sites |
| 2 | **Obligation lie** | describes maintenance nobody performs — "it grows whenever…" | a test enumerating the surface against the stand-in |
| 3 | **Configuration lie** | true on a build/platform we don't ship | run the claim on the shipped interpreter |
| 4 | **Coincidence** | true via a layer the claim doesn't mention | trace the value, not the sentence |
| 5 | **Parameter lie** | a flag honoured on one branch, ignored on another | assert its effect on every branch |
| 6 | **Promise gap** | the contract is narrower than what depends on it | a property test over the predicate/consumer pair |
| 7 | **Ambiguous meter** | one value, two meanings — success and absence read alike | a meter must not report them identically |
| 8 | **Unfalsifiable claim** | asserted reachability or adequacy, no control | a negative control, or delete the claim |
| 9 | **Partial guard** | enforced on one tier, hoped on the other | enumerate the tiers the guard covers |

## Status, when censusing

- **gated** — a `poe` task in `all` fails when it breaks. Name the task.
- **tested** — a test asserts it *and that test has a demonstrated negative control*. Name the test.
- **argued** — prose only. This is the debt, and the review's agenda.

Two numbers, never one: `argued = 0` and `never censused` must not read the same (class 7 applies to
the census itself). Do not census every docstring — scope to modules owning a **shared resource**: a
lock, a thread, a cache, a socket, a file another process maps, a process lifetime.

## What this is not

Not a prose linter. Claims are semantic and `.agents/rules/comments.md` already settles that
question — mechanize *shapes* (classes 1, 5, 9, and 2 via reflection), never sentences.

Not a completeness target. "All claims verified" invites re-labelling rather than checking, and some
claims should stay `argued` forever — a claim about intent is not always mechanizable, and
pretending otherwise is class 8.

## Two things the record shows

- **Three of the nine were found by an adversary attacking a write-up**, not by reading code fresh.
  The isolated pass is load-bearing, not decorative.
- **A census row can be wrong.** One asserted a lock-precondition violation that did not exist,
  while two *other* methods were mislabelled. A census is a claim too — hand it over to be attacked,
  not confirmed.
