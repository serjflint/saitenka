# Writing an AGENTS.md — the guide (vendored, self-contained)

An evidence-based guide to the `AGENTS.md` an AI coding agent reads in this repository. Reframed for an
**open-source project on git/GitHub** (saitenka's case) from an internal-monorepo original; the universal
core and the 2026 research (§ Evidence) are preserved, the internal-VCS/build/preset specifics are dropped.
Vendored here so the skill is self-contained — no dependency on an external file or another skill.

> `AGENTS.md` is the cross-agent open standard (freeform markdown, stewarded by the Linux Foundation's
> Agentic AI Foundation since Dec 2025). Most agents also read `CLAUDE.md`/`.cursor/rules`; serve them with
> one `AGENTS.md` + a one-line `@AGENTS.md` import or a symlink for the rest.

## 1. What it is — and is not

`AGENTS.md` tells an agent the few things it **cannot infer** and **must not get wrong**. It is not
documentation; it complements `README`/docs, it doesn't duplicate them. Freeform markdown — any "required
five-section schema" is content-farm folklore, ignore it. The agent already knows the public world (git, gh,
Python, pytest); re-teaching it is worse than useless (§3). In an open-source repo the *valuable* facts are
the **non-inferable** ones: a codegen/ownership boundary, a domain invariant a public model would "fix" as a
bug, a genuine footgun — each with its *why*.

## 2. The one rule: keep it minimal

Minimality is the whole game. The top reason is **conflict avoidance**:

> Every hardcoded line can *contradict reality*. A tool name, flag, or workflow pinned in `AGENTS.md` can
> conflict with what a **hook**, an **MCP server**, a **newer version**, or a **pluggable backend** actually
> provides — and agents follow written instructions **literally**. A stale or conflicting line steers the
> agent *wrong*, confidently. Fewer lines = fewer things that can disagree with reality.

Two measured reasons reinforce it:

- **Length itself degrades the model** — large capability drops as always-loaded context grows, even when
  the extra tokens are irrelevant. Paid every session. **[measured, Du 2025]**
- **Structure barely helps** — file size, ordering, and layout had *no* measurable effect on compliance.
  Spend effort deleting, not architecting. **[measured, McMillan 2026]**

**Rule of thumb:** a few dozen high-value lines. Past a screenful, content is in the wrong home (§4).

## 3. What earns a line — the inclusion test

Keep a line only if it passes **all three**:

1. **Non-inferable** — the model can't know it from public training (a codegen-owned dir, a real footgun, a
   domain invariant). ✗ Public tooling, language style, "clean code." Naming a genuine non-inferable repo
   fact is high-value: studies show agents use a named repo tool **50–160× more**.
2. **Stable & universal** — true across the repo, not environment/config-dependent. A **pluggable/optional**
   fact (which optional backend is active) fails this: hardcoding it in the always-loaded file is brittle
   and conflicts with setups that differ → route to a **rule**. Only the *safety half* (never `grep -r` the
   whole tree) is universal → make it a **hook**.
3. **Not enforceable elsewhere** — if a linter, type-checker, CI job, hook, or gate can guarantee it, let
   them. Prose is an unenforced suggestion; a check is a guarantee.

**Good lines** (non-inferable + stable + un-enforceable, ideally *with the reason*):

- "`<generated dirs>` are codegen-owned and overwritten — edit `<source>`, regenerate." — a boundary the
  model will otherwise violate.
- A framework/domain invariant a public model would 'fix' as a bug, with a pointer to the file that owns it.
- "Never run `grep -r`/`find` over the tree (it hangs here)." — safety; also enforce as a hook.

**Delete or move:** "use git" · style rules · a project overview / architecture narrative · a tutorial · a
changelog · *which* pluggable tool to use (→ rule).

## 4. Where everything else goes (routing) — bias: enforceable > written

| Candidate | Put it in | Why not always-loaded prose |
|-----------|-----------|------------------------------|
| Formatting, naming, style | Formatter + linter (CI) | A machine rewrites it; prose dilutes attention. |
| A hard checkable ban (forbidden call, edit generated dir, raw `grep`) | A **tool-hook** that blocks the action (+ CI) | Enforcement is a guarantee; prose is advisory. |
| A deterministic pass/fail check | A **gate task** (required vs advisory) | A check is reproducible; prose is not. |
| "How to add an X" recipe | A **skill** | Loads on demand; doesn't tax every session. |
| Deep reference / architecture / rationale | `README`/docs, linked | Humans need it; the agent opens it when relevant. |
| *Which* pluggable/optional tool to use | A **path-scoped rule** | Hardcoding invites conflicts (§2). |
| Live API/config the agent queries | An **MCP server** | A tool call beats a paragraph that goes stale. |

## 5. Length, structure, and drift

- **Short beats long** (§2). No evidence longer helps; strong evidence it hurts.
- **Don't cargo-cult structure.** Put things where a *human maintainer* can find and prune them.
- **Front-loading is a weak hedge, not a fix** — cheap to put the one critical line first; not a substitute
  for brevity.
- **The real limiter is within-session drift**: compliance decays as the agent generates more code — a
  static file can't fix it. Mitigate with smaller task scope and **hooks that re-assert critical rules at
  the moment of action**.

## 6. Nesting

- **Closest-file-wins:** the nearest `AGENTS.md` takes precedence and *replaces*, not merges. (saitenka:
  the workspace root, the repo root, and any subtree can each hold one.)
- A nested/leaf file earns its place **only** for a non-inferable invariant specific to that subtree; if the
  rule is path-triggered, a **path-scoped rule** usually beats a whole nested file.
- **Truncation caveat:** Codex concatenates nested files to a **32 KiB** cap and, on overflow, silently
  truncates the nearest/leaf file first. Keep each small; **enforce via a hook** any safety-critical line
  rather than trust it to survive in a leaf.

## 7. Keep it fresh — a stale line is worse than none

- **Review it in the same PR** that changes the convention it describes.
- **Lint/smoke it** — a check that every path/command it mentions still exists catches the most common rot
  (a `wc -l` cap is a fine start; a skill's `scripts/smoke.sh` rot-guards the symbols it cites).
- **Smoke-test occasionally:** give an agent a real task; if a line makes no difference, it's dead weight.

## 8. Anti-patterns (the tells)

- **Restating the linter/README** → delete.
- **Vague virtue** ("write clean code") → pure attention tax.
- **Hardcoding a pluggable/optional tool** → the conflict failure mode (§2); move to a rule.
- **A prohibition with no rationale** → drifts; add the *why* or make it a check.
- **A prose rule a hook could enforce** → convert it.
- **Prescribing a tool as if it exists** → don't claim "the linter catches X" unless it does; write the
  check or say it's manual.
- **A "just in case" nested file / boilerplate** → each file must carry a non-inferable line or not exist.

## Evidence base (persisted 2026 research)

Primary research behind this guide; tags mark **[measured]** vs **[reasoning]**.

- **Context files help only for what the model doesn't already know** — Gloaguen et al., *Evaluating
  AGENTS.md*, arXiv:2602.11988 (ICLR 2026 wkshp). LLM-generated files net-negative on Python (−0.5…−2%,
  +20–23% cost); dev-written +4%; naming a repo tool → **50–160×** more use (agents follow files literally).
  **[measured]**
- **Length itself hurts** — Du et al., *Context Length Alone Hurts Despite Perfect Retrieval*,
  arXiv:2510.05381 (EMNLP 2025 Findings). **13.9–85%** degradation as input grows, even under oracle/masked
  retrieval; includes coding. **[measured]**
- **Structure doesn't move compliance; within-session decay does** — McMillan, arXiv:2605.10039. 1,650
  Claude Code sessions; size/position/architecture = null; real effect ≈ **5.6% lower compliance per
  generated function**. **[measured]**
- **More simultaneous instructions → worse adherence** — IFScale, arXiv:2507.11538. **68%** of 500
  followed; primacy bias. **[measured]**
- **Nested-file truncation** — Codex silently truncates combined `AGENTS.md` at **32 KiB**, leaf-first
  (openai/codex #7138, #13386). **[measured]**
- **Freeform standard & progressive disclosure** — agents.md spec (freeform, no schema; AAIF/Linux
  Foundation Dec 2025); Agent Skills load name+description until invoked; path-scoped rules attach on match.
  **[reference]**

**Central thesis — "minimal, to avoid conflicts with optional tooling" — is [reasoning]**, built on the
measured literal-following (Gloaguen) + length (Du) and drift (McMillan) results. The line-count target is
**[inferred]**, not a measured optimum — validate on the repo before treating it as a hard rule.

**Corrections logged during the original research (don't re-trust secondary web):** "AGENTS.md requires a
five-section schema" — fabricated by SEO farms, the spec is freeform. "adherence degrades ~4–7% per 1,000
tokens" — no such study, invented. ContextCov's "88.3% vs 67.0% compliance" — a WebFetch abstract-summary
hallucination, not in the paper. Verify numbers against the full paper, and verify before dismissing.

## Provenance

Distilled and reframed (open-source/git audience) from an internal-frameworks `HOWTO_AGENTS.md`; the
research is 2026 primary sources, fact-checked against the arXiv papers and the agents.md spec.
