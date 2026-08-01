<!--
AI Policy.

Adapted for Saitenka from django-modern-rest's AI policy
(https://github.com/wemake-services/django-modern-rest/blob/master/.github/AI_POLICY.md),
which is itself copied from https://github.com/astral-sh/.github/blob/main/AI_POLICY.md (MIT).

Saitenka DIVERGES from the upstream policy on one point, deliberately: this project's code is
almost entirely agent-written, and that is the intended mode. What we keep from upstream is the
half that matters — humans stay in the loop, humans own the result, and humans do the talking.
-->

# AI Policy

Saitenka is built with AI. The code is almost entirely written by coding agents, and that is
the intended way to contribute — **but only with a human actively in the loop.** You remain
responsible for any code you publish, and the maintainer is responsible for anything merged and
released. We hold a high bar for all contributions.

The distinction that matters is not human-vs-agent authorship; it is **supervised vs.
unsupervised**. Unsupervised, unreviewed agent output is slop. It is not useful to this project
and will be closed. For every contribution, a human must:

- Define the problem and own the user value.
- Direct the design decisions — the agent proposes, the human chooses.
- Inspect the results and **understand** the tests, goldens, risk output, and profiles — not just
  that they are green, but why.
- Accept responsibility for the merged result. The human who merges owns every line, agent-written
  or not.

## Humans talk; agents build

**AI must not be used to generate comments when communicating with the maintainer.** We expect
comments on issues and pull requests to be written by humans, in your own voice. We may hide any
comments we believe are AI-generated.

- If you open an **issue**, describe the problem in your own words.
- If you open a **pull request**, be able to explain the change in your own words — the PR body and
  your replies to questions. **Do not paste agent output when replying to the maintainer.**
- If you want to include context from an interaction with AI, put it in a quote block (`>`),
  disclose it as such, and accompany it with your own commentary. Do not paste long snippets.

We understand AI is useful when writing in a non-native language. If you use it to edit your
comments, take the time to ensure the result reflects your own voice and ideas; if translating,
write in your native language and include the translation in a quote block.

## Attribution trailers

Strip agent-attribution trailers before submitting — no `Co-Authored-By:` lines naming a coding
agent, no "Generated with …" footers on commits or PR bodies (this matches the repo's standing
`AGENTS.md` rule). You are the author of record; the agent is a tool.

*Respectfully derived from the [Astral](https://github.com/astral-sh/.github/blob/main/AI_POLICY.md)
and [django-modern-rest](https://github.com/wemake-services/django-modern-rest/blob/master/.github/AI_POLICY.md)
policies, with the autonomous-agent stance intentionally inverted for this project.*
