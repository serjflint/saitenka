# Contributing to Saitenka

Thanks for wanting to improve Saitenka. This is a **single-maintainer** project
(`serjflint/saitenka`), so "ready" means *ready to merge to `main`* — the latest shippable state is
always `main`. Please also read the [Code of Conduct](CODE_OF_CONDUCT.md) and the
[AI Policy](.github/AI_POLICY.md) before contributing.

A contribution is ready only when its user value, contract, implementation, tests, documentation, and
failure behavior all agree. Passing tests is necessary but not sufficient.

## How to work in this repo

The standing rules live in **[`AGENTS.md`](AGENTS.md)** — Python-via-`uv`, Anki access, the LLM policy,
tokenizer/golden traps, the dev gate, refactoring seams, and the testing invariants. This guide is the
*contribution* layer on top; it points at the canonical sources rather than restating them:

- **Run the app / manual QA:** [Development docs](https://saitenka.readthedocs.io/en/latest/contributing/development/)
- **Module map & data flow:** [`overlay/ARCHITECTURE.md`](overlay/ARCHITECTURE.md)
- **The dev gate and its env traps:** the `dev-gate` skill (`.agents/skills/dev-gate/`)
- **Writing a test the house way:** the `write-test` skill (`.agents/skills/write-test/`)
- **What actually runs:** `pyproject.toml` `[tool.poe.tasks]` (the authoritative task list)

## AI-assisted contributions & human accountability

Saitenka's code is **almost entirely agent-written, and that is the intended mode** — but only with a
human actively in the loop. The distinction that matters is not human-vs-agent authorship; it is
**supervised vs. unsupervised**: unsupervised, unreviewed agent output is slop and does not merge. See
the [AI Policy](.github/AI_POLICY.md) for the full statement. In short, a **human** must:

- Define the problem and own the user value.
- Direct the design decisions — the agent proposes, the human chooses.
- Inspect the results and **understand** the tests, goldens, risk output, and profiles.
- Accept responsibility for the merged result — every line, agent-written or not.

**Humans talk; agents build.** Conversation in issues and PRs — motivation, scope, review back-and-forth,
accept/reject — is done by the human, in their own voice. Agents do the work (code, tests, evidence, the
PR body) and the isolated review; they do not post issue/PR discussion or speak for the maintainer.

## What makes a contribution ready

1. Solves a concrete, demonstrated user problem (a mining / reading / rendering workflow that fails or is
   missing today).
2. Has a narrow contract stateable without referring to its implementation.
3. Reuses existing seams (`Fake*` transports, `render.flow`, `sub_index`, the deinflect chokepoint,
   `anki.build_note`) rather than adding a dispatcher condition.
4. Preserves existing behavior by default; a golden move or behavior change is intentional and blessed.
5. Fails visibly when a dictionary, subtitle, socket, display, or Anki assumption is wrong.
6. Tests the observable outcome — return value, emitted IPC message, written note dict — not a private
   attribute or a mock call-count.
7. Documents the real trust / compatibility / caching / golden behavior without overstating it.
8. Is one coherent Conventional-Commit-sized decision, small enough to review at once.

## Workflow

Work trunk-based: small focused commits, no long-living branches, branch as `issue-$N` when an issue
exists. A branch that can't stay rebased on a green `main` is too big — split it.

### 1. Establish the user value

Record the affected workflow, the exact behavior on `origin/main`, the expected behavior after, and a
reproducer. Then satisfy the baseline evidence for your change type:

| Change type | Required baseline evidence |
| --- | --- |
| Bug fix | A regression test fails on the base SHA for the stated reason and passes on the branch. |
| User-facing feature/config | A demonstration that the scenario is unavailable or fails on base and works on the branch. |
| Internal feature | Concrete unavailable base behavior + branch acceptance proof. |
| Refactor | The same acceptance suite passes on base and branch, plus an invariant / complexity-snapshot / seam-clarity reason. Navigate by symbols (LSP); use a codemod for mechanical edits. |
| Tokenizer / golden change | The `unidic-lite`/dictionary bump that legitimately moves the golden, plus a deliberate re-bless — never a regenerate-to-green. |
| Documentation-only | A source-backed error or reader failure, then link/command/reader proof of the fix. |

Fail early if the change can't name a concrete before/after, or if a new knob is not consumed by the
production workflow it claims to change.

### 2. Define the contract, then choose the smallest design

State defaults, precedence, valid/invalid values, compatibility (does it move a golden, invalidate a
cache, need a re-index?), and what's logged when the behavior is skipped or degraded. Prefer an existing
registry / protocol / `Fake*` over a dispatcher condition; keep dictionary- and platform-specific logic
out of generic core; don't fold an unrelated cleanup into a feature. For LLM changes, readings and pitch
stay grounded in dictionaries — never model output. For concurrency changes, no cross-test/worker shared
mutable state (the free-threaded `poe test-ft` run is the check).

### 3. Test and run the gate

Follow the **write-test skill** and the **Testing** invariants in `AGENTS.md`: test the seam not the
shell, assert observable behavior, construct over fixture (function scope only), `monkeypatch` not `mock`,
the plain→`dirty-equals`→golden assertion ladder. Cover default/no-op, valid config, invalid/boundary
values, precedence, batch behavior, cache/golden, and the security-shaped inputs for your surface
(tokenizer/de-inflection, the `parse_cues` no-raise fuzz contract, the mpv IPC seam via `FakeMpvServer`,
Anki via `FakeAnki` against a **copy** of `collection.anki2`).

The pre-push gate is **`uv run poe all`** (see the dev-gate skill for the task-by-task breakdown and the
free-threaded / 3.13-pinned-env traps — don't run `uv run --python 3.13` against the default `.venv`).
Inner loop: `uv run poe affected`. Adequacy beyond the unit suite (opt-in, not in the gate):
`uv run poe mutate | fuzz | crosshair`.

**Run repowise self-review on your own diff first** — it's the same non-LLM signal a reviewer sees:

```bash
cd .. && repowise risk origin/main..HEAD    # change risk over the diff
uv run poe perf-risk                         # repowise health on the overlay
```

A change to a high-churn hotspot (top of `repowise risk`) welcomes closer scrutiny and mandatory tests
for the touched path.

### 4. Fresh adversarial review

Before merge, review the diff in a fresh, isolated context that did not author it — the **Sharpen loop**
(`.agents/sharpen/`, author→skeptic→judge, two independent UPHOLDs) or `/code-review`. Give it only the
base, the PR title/description, the diff, and the claimed validation — no prior rationale. Fix every
P0/P1 (correctness, data loss, false contract, inert feature, FT race); resolve or explicitly accept P2.
Any code change invalidates a prior pass. Prefer reviewers from a different model family than the author
when the host roster makes that available: different families reduce correlated blind spots. This is a
recommendation, not a validity condition — two genuinely isolated reviewers still satisfy the review
gate when cross-family routing is unavailable. Deterministic gates remain mandatory either way.

### 5. Prepare the change package

Commit as focused **Conventional Commits** with optional scope, imperative subject under ~72 chars, and
**no agent-attribution trailers** (see the AI Policy). Open the PR with the
[template](.github/PULL_REQUEST_TEMPLATE.md); keep the body lean and why-focused. Also, when applicable:

- **`CHANGELOG.md`** — draft with `uv run poe changelog` (git-cliff), then hand-review; user-visible
  changes always get an entry.
- **Docs build** — for doc or user-visible changes, `uv run poe docs` (`mkdocs build --strict`) must pass.

## Saitenka adversarial failure patterns

Mandatory review prompts for this codebase's surfaces:

| Failure pattern | Question to answer |
| --- | --- |
| Inert configuration | Which production consumer changes behavior because of this value? |
| Partial wiring | Do CLI, the mpv IPC path, AnkiConnect, the `collection.anki2` copy path, and the reader agree? |
| Compatibility invariant regression | Did a new field preserve positional construction, immutability, equality, hashing, serialization, and stable order of the render/IPC/note dataclasses? |
| Golden drift smuggled as a fix | Was a moved golden a deliberate re-bless, or a regenerate-to-green hiding a behavior change? |
| De-inflection matching trap | Does a surface form that de-inflects to multiple lemmas resolve correctly, and is the reading from the dictionary — never guessed? |
| LLM grounding overclaim | Can a reading/pitch/fact reach the user from model output instead of a dictionary? |
| Parser fragility | Does `parse_cues` return a possibly-empty list for *every* input, never raising? |
| Silent degradation | How does the user learn a dictionary was missing, a subtitle track empty, a socket closed, or a mock substituted? |
| Free-threading data race | Is there cross-test/worker shared mutable state? Does the seam survive `poe test-ft` under `PYTHON_GIL=0`? |
| Live-DB / destructive Anki access | Does any path read the live `collection.anki2` while Anki is open, or write where a copy was required? |
| I/O glue unit-tested as core | Is a subprocess/socket/display boundary being unit-tested instead of exercised through a `Fake*`? |
| Mock-count assertion | Does a test assert a call-count or private attribute instead of an observable return/message/note? |
| Complexity ratchet dodge | Was `complexipy-snapshot.json` regenerated to silence a regression rather than after a deliberate refactor? |
| Copyleft leak | Did a new dependency add copyleft to the graph? (Only our own GPL `deinflect` is allowed.) |
| Documentation drift | Do README, the docs site, ARCHITECTURE, the skills, and runtime behavior say the same thing — one canonical source, the rest pointing? |

## Readiness checklist

- [ ] Base `origin/main` fetched; concrete before/after recorded.
- [ ] Change-type baseline evidence satisfied; branch acceptance passes.
- [ ] Tests cover the affected workflow and interactions; goldens re-blessed deliberately if moved.
- [ ] `uv run poe all` green; adequacy checks run where they apply.
- [ ] `repowise risk` / `poe perf-risk` reviewed; hotspot changes carry tests.
- [ ] Fresh adversarial review passed — zero P0/P1, P2 resolved or accepted.
- [ ] `CHANGELOG.md` updated and `poe docs` builds, for user-visible/doc changes.
- [ ] Conventional Commit, no attribution trailers, PR body written in your own words.
