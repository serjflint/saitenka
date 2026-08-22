# Claim classes

"Verify the claims" is unactionable. These nine shapes are, because each needs a *different* check.

The unit of verification is never the sentence. Take the one this taxonomy was built from, as it read
at `502b9926^`:

> `_render_band`: "Rasterise row `i`'s band WITHOUT touching shared state — safe on a worker thread.
> **The caller stores the result under the lock.**"

Accurate about the function. Every caller rastered *inside* the lock, and the docstring's own
precision is what made it invisible — a reader checking the function agrees and moves on. The unit is
**the sentence plus the thing it constrains**, which is usually somewhere else.

| # | class | what it looks like | the check it needs |
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

The right-hand column is **what the class needs**, not what exists. Two are built here — class 1 as
`sgconfig/rules/no-raster-under-panel-lock.yml` (`812ba04f`) and class 2 as
`tests/test_runtime_stand_in.py` (`16e07012`). The other seven are shapes to look for, and saying so
is the difference between a taxonomy and a status board.

Note what the class-1 rule does *not* catch, because it is the lesson twice over: the rule is
lexical, so it sees `_render_band` under `with self._lock` and misses `_ensure_bands`, which rasters
under its *caller's* lock (deliberately — non-body rows are cheap and `render` is the measure). A
guard against a scope lie can itself be a partial guard.

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

## The census is itself a claim

Row B11 of the first census (`.agents/architecture-review/runs/2026-08-22-render-banded.md`) asserted
a lock-precondition violation that did not exist — while two *other* methods were genuinely
mislabelled and the census had missed both. Hand a census over to be attacked, not confirmed, and
write the correction back.
