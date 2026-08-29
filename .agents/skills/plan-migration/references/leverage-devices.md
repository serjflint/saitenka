# Leverage devices — the three that change a migration's per-site cost

A device earns its place only if it moves the *unit price*. Everything else is scheduling.

## Facade — one new surface, callers unchanged

Put the destination behind a single new object and let the old surface delegate to it. Cost: O(1)
to build, plus a delegation layer that must later be deleted (the deletion is real work — plan it).
Pays when the sites are many, the destination is one, and the sites' shapes differ enough that no
single rewrite fits them.

The failure: the facade becomes permanent. A facade with no scheduled deletion is a second home for
the same fact, which is the drift bug AGENTS.md "Documentation" names.

For a responsibility migration, distinguish a **compatibility facade** from a **field-only extraction**.
The old public entry point may legitimately remain, but authoritative state and transition decisions must
terminate behind it. A new object that stores fields while the host still decides admission, completion,
fallback, publication, and close has changed layout, not ownership; do not count it in the retirement meter.

## Shared port — one value carrying the facts several sites read separately

Several names over one owner are one port with fields, not N parameters. The arithmetic is the whole
argument:

    per-site conversion   O(sites)
    port + conversion     O(1) + O(clusters)

So the port pays exactly when sites cluster — and whether they do is a *measurement*, not an
intuition. `poe cluster-map` answers it: it resolves each host member to the fact underneath, so a
cluster reading sixteen members may be reading far fewer facts, and the "too big for a value"
judgement made from the raw count is simply wrong.

Two traps:

- Evaluating "would a port help?" on **one** cluster and applying the answer globally. Clusters
  differ; the column is per-cluster.
- Counting names instead of facts, which inflates the coupling and rejects the port that would have
  worked.

## Codemod — the mechanical part, done once

When the edit is uniform, write the transform instead of the edits:
[`codemod-recipe.md`](codemod-recipe.md). Cost: hours to author, then O(1) per additional site — so
it pays above a threshold that is lower than it feels, because the *review* of a codemod diff is
also cheaper than the review of the same edits made by hand.

The residue is the point: what the transform declines to touch is the set that needed a decision,
and enumerating it is free once the transform runs with `--check`.

## Not a leverage device: a ratchet

A ratchet (`poe host-arity`, `poe host-mass`) refuses regression and makes
progress visible. It is a **safety device**. It does not make one site cheaper to convert, and a plan
that lists it as its answer to "how will this get cheaper" has not answered the question.

## Recording the call

In the plan, name the device chosen **and** each device rejected, with a one-line reason. A rejection
with no recorded reason is re-read months later as a rejection of something else — one such
misreading kept a facade unevaluated for a whole migration.
