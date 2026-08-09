# Research prompt kit

Reusable skeleton for the `research` skill. The **living, filled-in library** is kept in the
maintainer's private notes as `research-prompts.md` plus the `*-research-prompt.md` / `*-r2.md` /
`*-research-findings.md` artifacts beside it — read the closest one and copy it. Two are worth reading
for structure: `geometry-primitives-research-prompt-r2.md` (adversarial-verify a converged winner) and
`gemini-sota-prompt.md` (a tailored SOTA survey). This file is the self-contained fallback.

## Prompt shape — a Role + XML markers

Every prompt is self-contained (the external system has no context on us) and structured with a **role**
and **XML-tagged sections** so a deep-research model can't blur context into task into rules. The skeleton:

```
<role>
You are a skeptical <domain> analyst. Your job is NOT to confirm a list or a consensus — it is to find
what's missing / where it's wrong, and to state each item's real maintenance status. Prefer primary
sources (repo, releases page, last-commit date, PEP/docs). A confident answer with no link is worthless;
treat anything you can't link as UNVERIFIED.
</role>

<context> ...who I am, my flow, my stack, the specific gap — tailored, not generic... </context>

<known_tools note="go BEYOND these, don't re-describe them"> ...the list... </known_tools>

<task name="..."> ...one focused question per task tag... </task>

<verification_rules>
- Primary source or it didn't happen: a link + a date per claim.
- Never invent: no fabricated tools/versions/repos; if you can't link it, tag UNVERIFIED. (Past runs
  invented plausible names — name known fabrications so the model recognises the failure mode.)
- Attack the consensus / go beyond the known list — the value is what's missing or wrong.
- Tag every claim [confirmed: link] or [could not verify]. Tradeoffs + dissent, not hype.
</verification_rules>

<output_format>
- One-paragraph verdict up top.
- Per-item table/list, each row tagged [confirmed: link] / [could not verify], with maintenance status.
- A "Missing / under-specified" section.
</output_format>
```

**Output contract** (inside `<verification_rules>` / `<output_format>`): links, maintenance status /
last release, a one-line verdict *for my flow*, dissent not hype, current-year sources, and the
per-claim `[confirmed: link]` / `[could not verify]` tags. Sharpen to an `-r2` when a run is
over-filtered or shallow — restate as *"new constraints override where they differ"*.

## Grounded discovery (run alongside the prose sweep)

An LLM sweep hallucinates tools and, anchored on a known list, only confirms it. A registry search cannot
fabricate — run it in parallel and merge:

```
gh search repos --topic sentence-mining --topic anki --topic texthooker --sort stars --limit 50 \
  --json fullName,stargazersCount,pushedAt,description,isArchived
gh api repos/<owner>/<repo> --jq '.pushed_at, .stargazers_count, .archived'
gh api repos/<owner>/<repo>/releases/latest --jq '.tag_name + " @ " + .published_at'
gh search repos --owner <org> --sort stars --json fullName,stargazersCount,pushedAt,description
```

Filter to `pushedAt >= <recent>`, unarchived, dedupe against the known set, rank by stars. Use **three
angles** — they cover different slices: `--topic` (misses untagged repos), keyword query (misses
oddly-named), and `--owner <org>` (catches an ecosystem once you've found one sibling). The topic pass
found `Memento`; the keyword+owner pass found `mpvacious` and the whole Ajatt-Tools cluster the topic pass
could not see.

## What survives → findings

`<topic>-research-findings.md` in the maintainer's private notes: only claims that passed the gate, each with its
citation and maintenance status; a "Missing / under-specified" section; and the GO items / issues to
file. Round-1 consensus that failed verification is recorded as *rejected*, with why.
