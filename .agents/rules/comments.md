# Rule: comments and docstrings carry an information delta

This codebase is agentic from day one, so comments trend toward LLM over-explaining: narration of
what the line does, and a record of how it got that way. Both are noise that outlives its subject.
Treat comment bloat the way you treat cognitive complexity — something to cut, not to preserve.

## Cut

- **Process scars.** `(WP5.3)`, `(plan R4)`, `Stage N`, "as discussed", "the review found", "P1-2",
  "this used to be three fields". The history is in git; a reader needs the invariant. A scar also
  goes stale silently — "executed here (WP5.3)" survived the code moving somewhere else.
- **Zero-delta echoes.** `# loop over the dicts`. If the sentence restates the signature, the
  heading, or the command name, delete it.
- **Superlatives and rankings.** "the widest port so far", "the last one", "the only remaining" —
  each is a claim about the rest of the tree that nothing keeps true.
- **Counts of anything that moves.** A member count, a row count, a port width. Two such numbers
  went stale inside a single session here. If a generator prints it (`poe arch-map`), point at the
  generator; if none does and the number
  matters, that is an argument for a meter, not for a comment.

- **Anything the code already is.** If a value, a rule or a list is stated in code, a test, a
  manifest or a generator, the comment points at it — it does not restate it. Two copies of a fact
  drift independently and one goes stale silently, and the prose copy is always the one nobody
  updates. Prefer the code as the source of truth and let the comment carry only what the code
  cannot: why it is that way.

## Keep

The *why*, a gotcha, a constraint, a refuted alternative, a bug/PR reference. A number that is
**evidence inside an argument** stays, because it stops being true only when the argument does
("reads eight fields to snapshot one object"). A number **reporting the state of the tree** goes.

The sharpest test of a surviving comment is Ousterhout's (*A Philosophy of Software Design*, ch. 13):
a comment must sit at a **different level of detail** than the code it sits on — higher (the intent,
the contract) or lower (units, bounds, a constraint the types cannot express). A comment at the
*same* level is a restatement, and that is the whole of the "information delta" rule in one test.

## Distil what survives

One tight clause beats a paragraph. No teaching tone, no narrative, no hedging. A long comment is a
smell: compress it or justify it.

## Not a gate

"Echoes the code" is semantic and not AST-matchable, so this is a review discipline rather than a
`poe` check. AGENTS.md **Comments** and **Documentation** carry the same rule for prose; this file
exists so it loads without anyone opening AGENTS.md first.
