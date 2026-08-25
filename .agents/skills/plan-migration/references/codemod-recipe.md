# Codemod recipe — worklist, transform, residue

Two commands and one file. The harness is `tools/codemods/harness.py`; the worked example is
`tools/codemods/move_member.py`, which moves a flat host member onto the object that holds it.

## 1. Resolve the member, and look at its sites

```sh
uv run python tools/cluster_map.py --member <name>          # what it is, what it reads, every site
uv run python tools/cluster_map.py --member <name> --json    # the same, as the worklist
```

The site list is an **attribute** match, not a text match: a same-named parameter and a mention in a
comment are not sites. A codemod driven from `git grep` edits both.

Read the `kind` and `fact` lines before writing anything. A member that resolves to a slice of one
owner is part of a cluster, and converting it alone is the per-site price the plan was trying to
avoid.

## 2. Write the transform against LibCST

```sh
uv run --group codemod python tools/codemods/move_member.py <old>=<owner.field> --check
```

LibCST, not a regex and not `ast.unparse`: formatting, comments and the goldens survive the round
trip, and a diff that is the whole file is not reviewable. The `codemod` group is opt-in — its build
has no free-threaded wheel, so it stays out of the default env and every codemod runs under
`uv run --group codemod`.

A new transform reuses `harness.worklist` (the `cluster_map` handoff) and `harness.apply` (the
apply/`--check` loop) and supplies only the `leave_*` method that does the rewrite.

## 3. Prove it finished, then read the residue

`--check` reports what *would* change without writing. A finished transform reports **zero** on a
second run; a transform that cannot say that has no way to prove it is done.

What the transform declined to touch is the residue, and it is the valuable output: those sites
needed a decision, and the plan's next section is that list. Do not hand-fix them inside the codemod
run — the mixed diff hides both halves.

## 4. Land it as one commit per family

The family converts together or the ratchets read the half-converted state as progress. Regenerate
any census in the same commit (`poe host-arity`, `poe host-mass` auto-tighten), and let the gate's
diff be the review of the conversion's completeness.

After the zero-residue check and permanent forward contract pass, remove a one-shot transform from the
final tree. The PR history preserves the migration; `harness.py`, `move_member.py`, and another genuinely
reusable transform remain only when they lower a future migration's unit price.

## Soundness, before anything runs

`move_member` rewrites the attribute wherever it appears, which is sound **only** when every
receiver in the tree is the host. `--member` lists the receivers; when one is something else, type
the receiver in the transform instead of widening the match. This is the one place a codemod can
silently corrupt a tree, so it is checked first, not last.
