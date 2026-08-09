---
name: research
description: >-
  Run grounded web-research the house way — prior-art / SOTA / competitor sweeps where an
  external deep-research system *proposes* and WE *verify* against fetched sources and our own
  repo/memory before trusting anything. Use when told to research prior art, survey the field,
  do a SOTA or competitor sweep, compare tools/approaches, "is X state of the art", find what
  already exists, or check a rival's recent releases. Assembles the shared project context,
  authors and sharpens the prompts (wide first, an r2 when a run is over-filtered), then runs a
  mandatory fact-check pass: every named tool/claim needs a fetched link plus maintenance
  status, fabrications flagged, dissent over hype. Backend-agnostic — any web-search system.
  NOT for a codebase-internal question (use repowise/LSP), reading one known URL (just
  WebFetch), or the diagnosis→PR loop (use contribute).
metadata:
  project: saitenka
---

# research

Grounded web-research: the external system **proposes**, we **verify**. The failure this exists
to prevent is documented in our own runs — a deep-research round-1 consensus here was *"flatly
wrong on its most critical claims"*, and earlier runs surfaced fabricated tools
(`Chimahon`, `Yomihon`, `Manatan`, `PopLingo`). **The value is the verification gate, not the
prompting.** An unverified research brief is a hypothesis, never a result.

## Loop

1. **Assemble context.** Prepend the shared project-context block (who / daily flow / constraints /
   priorities) so a run is tailored to *this* flow, not generic — and **demarcate core vs
   already-solved**: name the differentiators worth researching and the peripheral bits already
   handled (e.g. audio/screenshot via ffmpeg/mpv), or the run burns a thread chasing a
   non-differentiator. Under-specified context is the top cause of wasted research. Canonical living
   library + context: kept in the maintainer's private notes; a reusable skeleton + the output
   contract + the fact-check pattern are in [`references/prompt-kit.md`](references/prompt-kit.md).
2. **Author + sharpen.** One self-contained prompt per gap, structured as a `<role>` + XML-tagged
   sections (`<context>` / `<known_tools>` / `<task>` / `<verification_rules>` / `<output_format>`)
   with per-claim `[confirmed: link]` / `[could not verify]` tags — skeleton in
   [`references/prompt-kit.md`](references/prompt-kit.md). Each carries the **output contract**: links,
   maintenance status / last release, a one-line verdict *for this flow*, tradeoffs + dissent (not
   hype), prefer current-year sources. Run wide first; write an
   `-r2` with overriding constraints when a run comes back over-filtered or too shallow.
   **Run a grounded discovery pass in parallel** — `gh search repos --topic <t> --sort stars`
   (+ `gh api repos/<o>/<r>` for maintenance). It cannot fabricate, and catches what an LLM prompt
   anchored on a known list misses (this sweep found the direct competitor `Memento` that way after
   the prose sweep missed it).
3. **Handoff.** Write each prompt to the maintainer's private notes and **open it in VS Code** (`code <path>`),
   then hand it to the user to run across their web-search systems (backend-agnostic — run the same
   prompt in several and compare). Ingest the pasted reports; do not fabricate the external step.
4. **Verify — the gate.** For every named tool/claim: WebFetch the *actual* source (repo / release /
   docs), confirm it **exists** and its **maintenance status**, and verify **each atomic sub-claim**
   against the fetched text — not the whole claim at once, or a real repo with one invented sub-feature
   (a wrong license, a fake flag) sails through a claim-level check (FActScore, Min 2023). Cross-check
   against ground truth we own — the repo (repowise / LSP), memories, and the competitor clones in
   `~/workspace/`. Drop or flag anything unsourced or fabricated; never paper over "doesn't exist /
   unmaintained". For a large claim set, fan out skeptics with the Workflow tool — but prefer
   **primary-source** grounding to an LLM verdict: an LLM judge carries self-preference / family bias
   (over-rates its own family's output; worse in self-refinement — Xu 2024), so if you use judges, use a
   **different family than the generator** (shared training data ⇒ correlated blind spots; homogeneous
   agreement is not corroboration), require ≥2 independent sources for a high-stakes claim, randomize
   order, exclude self-judgment. Ground-truth (repo / `gh` / fetched source) has no such bias.
   All searching goes through the harness tools / `gh` / `WebFetch` — never shell `grep`/`find`
   (`AGENTS.md` → **Tooling** / the searching rule). The helper `scripts/verify.py owner/repo …
   https://url …` runs this grounding in one shot — existence (NOT-FOUND ⇒ fabrication), maintenance
   (abandoned = 3yr), latest release, link-resolves — and exits non-zero if anything is unverified.
   For a whole candidate list at once, `scripts/gh_audit.py owner/repo …` prints a triage table
   (stars · archived · 90-day commits · latest release · license → a per-repo verdict:
   MAINTAINED / STALE / ARCHIVED / ABANDONED / FABRICATED), same 404-gate exit — the insight layer
   above verify.py's pass/fail (it caught a report's "v2.0" claim whose real latest release was two
   years old).
5. **Triage the signals → widen / deepen / sharpen.** Read what the round actually returned and pick
   the next move — loop back to step 2, or finalize. Let the signal choose the action:
   - **Widen** when results only confirm known tools, a `gh search` topic surfaces a peer the prose
     missed, or a whole category is absent — add adjacent topics/categories, name knowns as *go beyond
     these*, drop the star/recency floor.
   - **Deepen** when a high-relevance hit is under-explored — fetch its code/releases, diff against our
     repo, and write a focused follow-up on that one tool's mechanism and gap.
   - **Sharpen** when results are noisy, hype, or off-target — tighten constraints (platform, in-mpv,
     offline), add explicit exclusions, restate as *new constraints override*.
   **Carry failures forward** (Reflexion, Shinn 2023): the findings note is episodic memory — the next
   prompt lists what was already checked / fabricated / 404'd as *don't re-suggest*, never a silent re-run.
   Stop when a round adds no new verified peer and no new gap.
6. **Aggregate.** A findings note (`<topic>-research-findings.md` in the maintainer's private notes) + GO
   items / issues — delta-only, **every surviving claim cited**. Separate evidence from folklore.
   Filed-issue text goes through `pr-ticket-describe`; a claim that survived becomes a GO item with its
   citation attached.

## Caveats — dos & don'ts (from our own runs)

**Do**
- Prepend the shared context; demand links + maintenance status + a verdict *for my flow* + dissent.
- Run wide, then sharpen (`-r2`) when over-filtered; run across several systems and compare their disagreements.
- Fact-check every named tool against a fetched link (the "Prompt 10" pattern) — existence + last release.
- Calibrate the verdict: a repo is **abandoned only after ~3 years** of no commits (a year quiet is still
  maintained), and a report's stale version/date is **cache lag, not an error** — set the current numbers
  yourself via `gh api`; they're ours to verify, never a mark against the report.
- Cross-check three lenses — the prose sweep, `gh search` (topic **and** keyword), and `gh api` — because each misses a different slice: the prose sweep hallucinates, topic-search misses untagged repos (it missed the 870★ `mpvacious`), keyword-search misses the oddly-named. `gh api` grounds every survivor.
- Split labor by strength: the external system does **broad web/community discovery + framing**; **fresh-repo enumeration (`gh`) and source/mechanism reading (`WebFetch`) are ours** — don't outsource to the slow system what a `gh`/fetch does trivially and faster. Ask the prompt for *pointers*, not for repo sweeps or code analysis.
- The fact-check cuts both ways — it also **rescues** a real tool wrongly dismissed. A hallucination watchlist can carry a false positive (`Manatan` was on ours; `KolbyML/Manatan` is real, 552★). Verify before dismissing, not just before trusting.
- Verify against ground truth we own: the repo, memories, the `~/workspace/` clones.
- Turn survivors into filed issues / GO items with the citation attached.

**Don't**
- Trust round-1 consensus or a single system — it is often flatly wrong on exactly the critical claims.
  **Convergence ≠ correctness**: LLMs share training data + exhibit family bias, so several can agree and
  all be wrong (we saw three passes "flatly wrong"). Treat cross-system **divergence** as a hallucination
  signal to chase to a primary source (SelfCheckGPT's insight), and **convergence** as unverified until grounded.
- Anchor the prompt tightly on your known list — it narrows the model to confirming the list and misses new peers; name knowns as "go beyond these" and add a `gh search` pass.
- Accept a tool name without a fetched link + maintenance check — fabricated tools are a recurring failure.
- Let an assumption stand where a source can be read — read the source.
- Act on a report's *summary* of a source — **download and read the primary source itself** before folding
  it in; a summary misattributes and overstates (a report here miscredited Zheng's LLM-judge paper to
  "Lee" and ALCE to "Wen"; both wrong against the actual arXiv pages).
- Ship hype; carry the tradeoffs and the dissent that survive verification.
- Re-describe what the reports say — the findings note carries only what survived the gate, cited.

## Verify

`scripts/smoke.sh` — asserts the skill's structure and that its references and pointers exist.
