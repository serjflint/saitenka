<!-- Vendored for skill self-containment. SSOT: ~/workspace/CONTRIBUTING.md — edit there and
re-vendor, never edit this copy. Snapshot 2026-08-04, source sha256:9435074957be0bdf. -->

# Strict contribution quality gate (generalized)

A project-agnostic, deliberately strict guide for deciding whether a change is ready to be
shown to maintainers. It sits **above** any repository's minimum checklist, not instead of it.

Passing focused tests is necessary but not sufficient. A contribution is ready only when its
**user value, contract, implementation, tests, documentation, and failure behavior all agree.**

AI-assisted changes follow the same standards as human-authored ones and must leave auditable
evidence for every claim they make.

## How to use this document

1. Read the target repository's own sources of truth first (below).
2. Walk the mandatory pre-review workflow (§1–§6), recording evidence as you go.
3. Fill the ready-for-review evidence record at the end.
4. Only after the gate passes and the human owner records **READY FOR MAINTAINER REVIEW** may
   upstream maintainers be requested or notified.

### Repository sources of truth

Before changing code, read whatever the project provides:

- Its `CONTRIBUTING` guide — setup, workflow, style, testing, PR expectations.
- Its pull-request template — required summary, linked issue, test plan, checklist.
- Its architecture/design docs — module ownership and extension points.
- Its user guide and public configuration reference — the user-visible contract.

If the project has no AI-contribution guide, that is not a lower bar: the same standards apply.

## Definition of a high-quality contribution

A high-quality contribution:

1. Solves a concrete, demonstrated user or maintainer problem.
2. Has a narrow contract that can be stated **without referring to its implementation**.
3. Uses the repository's existing ownership and extension points where they fit.
4. Preserves existing behavior by default unless an intentional breaking change is approved.
5. Fails visibly and actionably when configuration or runtime assumptions are wrong.
6. Tests the user outcome and interactions, not merely changed properties or helper calls.
7. Documents the actual trust, compatibility, caching, and migration behavior **without
   overstating guarantees**.
8. Is small enough to review as one coherent decision.

## Normative ready-for-review gate

The **human owner** is the person responsible for deciding whether the contribution is sent to
maintainers. A PR is **blocked from maintainer review** when any of these is true:

- An applicable gate in this document fails.
- A mandatory evidence row is blank; a conditional row lacks either evidence or `N/A — reason`.
- Any P0/P1 finding remains unresolved.
- Any P2 finding remains unresolved without recorded human-owner acceptance and rationale.
- Required local validation has not run, or an exception has not been approved and recorded.
- The target base has not been fetched and its SHA recorded for the final validation pass.
- The human owner has not recorded **READY FOR MAINTAINER REVIEW**.

"Maintainer review" means opening an upstream PR as ready for review, or requesting review on it.
Pushing a branch to a personal fork or opening a **draft** PR for CI is allowed — but it must not
notify or request upstream maintainers until this gate passes.

Terms used below:

- **Acceptance matrix:** applicable behaviors and environments, each with a test or a rationale.
- **Production-shaped fixture:** the real runtime data shape, with distinct, realistic inputs.
- **Complete suite:** all tests runnable in the documented local environment; otherwise the full
  required CI result plus the locally runnable suites.
- **Fresh reviewer:** a person or isolated agent session that did not implement the change and has
  not seen prior rationale or review conclusions.

## Mandatory pre-review workflow

### 1. Establish maintainer and user value

Before implementation for new work — and retroactively before review for an existing branch —
record:

- The affected users and workflow.
- The exact behavior on the current base branch.
- The expected behavior after the change.
- A reproducer, fixture, or command that demonstrates the gap.
- Related open and closed issues and PRs, including prior maintainer direction.
- Why existing configuration, extension points, or documented workarounds are insufficient.

Baseline evidence by change type:

| Change type | Required baseline evidence |
| --- | --- |
| Bug fix | Regression test fails on the recorded base SHA for the stated reason, passes on the branch. |
| Public feature / configuration | Linked issue or maintainer discussion, plus a demonstration that the user scenario is unavailable or fails on the base and works on the branch. |
| Internal feature | Concrete unavailable base behavior and branch acceptance proof; record why prior discussion is unnecessary. |
| Refactor | The same acceptance suite passes on base and branch, **plus** an invariant, structural metric, or maintenance problem proving value. |
| Documentation-only | Source-backed error or reader failure, followed by link/command/reader proof of the correction. |

**Gate:** fail if the PR cannot identify a concrete before/after outcome, or if a proposed knob is
not consumed by the production workflow it claims to change.

### 2. Define the contract before the implementation

For every public option, callback, environment variable, or behavior, specify:

- Meaning and units.
- Default and precedence.
- Valid and invalid values.
- Whether it configures a request, declares an expected response, or controls persistence.
- What happens when declared and observed values disagree.
- Backward compatibility, and whether reindexing, regeneration, or migration is required.
- Existing object invariants callers may rely on: positional construction, immutability,
  equality, hashing, serialization, deterministic ordering.
- Serialization boundaries (JSON checkpoints, job records, rehydration): define and use **one**
  explicit public snapshot API. Document whether generic helpers (e.g. `dataclasses.asdict`,
  `__dict__`, `vars()`) are supported; do not treat their private nested representation as a
  public contract implicitly.
- Which entry points (CLI, server, API, plugin, direct-library) must behave consistently.
- What is logged or surfaced when the behavior is skipped or degraded.

**Gate:** fail on dead configuration keys, mismatched declaration and output, silent downgrade,
or a downstream validator/cache that does not understand the new input.

### 3. Choose the smallest coherent design

- Prefer an existing registry, protocol, adapter, or specification over a dispatcher condition.
- Introduce a new extension point only when at least the current use case can be expressed
  generically and its contract is testable.
- Keep environment- and deployment-specific logic outside generic core code.
- Do not combine an unrelated cleanup with a feature unless maintainers requested the broader scope.
- Remove superseded branches, aliases, helpers, and documentation from the diff.
- Do not accept configuration that no execution path can consume.

**Gate:** fail if the PR title describes more than one independently reviewable decision, or if the
implementation hardcodes a specific product/instance name in a generic dispatcher without a clear
contractual reason.

### 4. Test in layers

Every PR must have a written acceptance matrix before review. Each potential path or boundary is
marked applicable with evidence, or `N/A` with a concrete reason. "Assess" does not always mean
"add a test"; it means consciously determine applicability and test every applicable behavior that
can regress.

**Baseline evidence.** Fetch the base branch, record its commit SHA and timestamp, and use that
same base for the diff, baseline, risk, and acceptance evidence. Satisfy the change-type rule from
§1. Do not use a property-only test when the claim is an end-to-end workflow.

**Focused unit and integration tests** cover:

- Default / no-op behavior.
- Explicit valid configuration.
- Invalid, zero, negative, malformed, non-finite, missing, and conflicting values where relevant.
- Precedence between argument, repository configuration, and environment.
- Multiple-item and batch behavior.
- Mismatch between declared and observed runtime values.
- Cache hit, cache invalidation, persistence, and reindex/regeneration behavior when affected.
- Interactions between features that share a budget, validator, cache, or fallback.
- Ordered budget/priority selection across a boundary sweep: increasing capacity must not evict an
  earlier-priority item in favor of a later one, or shrink its retained content; use unequal-size
  fixtures.
- Security-shaped inputs: delimiter text, instruction-like content, traversal-shaped paths, and
  untrusted contributor-controlled files.
- Observable errors, warnings, or provenance for skipped/degraded inputs.

Use production-shaped fixtures with distinct items, not repeated placeholders that avoid real
interactions.

**Repository checks.** Use the project's supported runtime and package manager (record the exact
versions — many repos pin a specific interpreter or toolchain). Run, at minimum, the project's
formatter, linter, and unit tests on the changed surface:

```
<sync deps>            # e.g. uv sync / npm ci / poetry install
<format --check>       # e.g. ruff format --check . / prettier --check
<lint>                 # e.g. ruff check . / eslint
<unit tests>           # e.g. pytest tests/unit / npm test
```

Validation levels:

1. **Always local:** dependency sync; formatter + linter on the repository (or on every changed
   file when the base carries unrelated formatting debt); focused regression tests; and every
   production-shaped integration test that exercises the changed workflow.
2. **Conditionally local:** broader tests importing or exercising changed production files,
   affected integrations found via test search / impacted-test selection, and frontend checks when
   frontend code or shared contracts change.
3. **Broad local suite:** useful once as discovery evidence, not a ritual prerequisite when it is
   dominated by unrelated offline retries, known base failures, or environment-only failures.
   Record the completed/partial result, runtime, interruption reason, and focused attribution.
4. **CI:** before requesting maintainer review, the repository's required jobs and its full support
   matrix must pass. CI supplies complete-suite evidence when an exhaustive local run is not
   proportionate or reliable.

Skipped tests are acceptable only when expected for the environment and unrelated to the change;
record their names and reason. Unexpected skips or failures block review. Record exact commands,
runtime, counts, skipped tests, failures, and CI links — do not summarize "tests pass" when only a
focused subset ran.

**Project self-review tooling.** If the project ships static-analysis, change-risk, impacted-test,
or health tooling, run it and record the output or disposition (`N/A — reason` when a file is
deleted/renamed/non-source).

**Gate:** fail if the change-type baseline is missing, any affected production workflow or feature
interaction is untested, the selected test set is not justified, or fixtures violate the production
contract they claim to prove. Required CI still blocks maintainer review.

### 5. Perform fresh adversarial review

Before human maintainer review, give a **fresh reviewer** only:

- The repository and target base.
- The proposed PR title and description.
- The PR diff.
- Claimed validation commands and results.

The reviewer may inspect repository code and contributor guidance read-only, but receives **no**
prior rationale or findings. Record reviewer type, timestamp, base SHA, head SHA, supplied
artifacts, findings, and dispositions. Approval is valid only for the exact recorded head — **any
code change invalidates the prior pass**; review the new diff in a different fresh person/session.

For agent-assisted review, request a concise verdict and cap a pass at ten minutes. A timeout is an
incomplete gate, not approval. Findings must identify a concrete contract violation, reachable
scenario, compatibility regression, or missing proof; stylistic churn alone does not justify an
endless review/fix loop.

Ask whether the change is useful to maintainers and other users; whether its motivation, scope,
contract, failure behavior, docs, and tests are coherent; and whether they would approve it.

Classify findings:

- **P0/P1:** correctness, data loss, security, false contract, inert feature, or central-workflow
  failure. Must be fixed.
- **P2:** important compatibility, observability, documentation, or boundary gap. Must be fixed or
  explicitly accepted by the human owner before review.
- **P3:** optional polish. May be deferred with rationale.

After changes, repeat review with fresh context. Do not tell the reviewer why earlier choices were
made; the PR must explain itself.

**Gate:** zero unresolved P0/P1 findings and no unacknowledged P2 findings.

### 6. Prepare the review package

Use the repository's PR template. The description must include:

- **Summary** — one to three bullets describing only the resulting behavior.
- **Why** — concrete user scenario and before/after; why the existing behavior or extension point
  is insufficient; related issue and maintainer discussion.
- **Contract and design** — defaults, precedence, compatibility, failure behavior, observability;
  trust/security boundaries and limitations; cache/persistence/reindex/regeneration implications;
  rejected alternatives and why, when the choice is non-obvious.
- **Test plan** — applicable baseline evidence and branch result; focused and broader validation
  commands with pass/fail disposition; format/lint results; relevant integration, risk,
  impacted-test, and health checks; any skipped validation and why.

Keep the PR description stable: do not churn it with local commit hashes, elapsed timings, or exact
passing-test counts. Put revision-specific diagnostics in the evidence record, CI, or review log.

For a **stacked** PR, name and link the parent, state merge order, and ensure the displayed diff is
only the intended delta. Prefer waiting for the parent to merge when upstream cannot review the
stack cleanly.

**Gate:** fail if the PR claims stronger guarantees than its implementation or tests establish.

## Communication economy — comments, descriptions, replies

Every code comment, issue, PR description, and review reply spends the reader's attention. Spend it
only on what the reader cannot recover from the artifact already in front of them. The test is the
same everywhere — **information delta**: if a sentence restates what the code, the diff, the linked
issue, or an earlier comment already says, it is cognitive load with no payload; delete it. What
survives must explain the **why** and the non-obvious context.

- **Why, not what.** Comment the reason, constraint, gotcha, trade-off, or reference
  (issue/PR/spec) — never narrate mechanics the reader can see. `# increment i` is noise; `# skip
  the header row the exporter always prepends` is signal.
- **One canonical source per fact.** State a fact where it is authoritative and point to it
  elsewhere; do not copy a command, version, or step into a second place where the two will drift.
- **Code comments** justify a line's existence: a non-obvious interaction, an invariant, or why the
  obvious simpler thing is wrong. Delete comments that echo the statement they sit on.
- **Issue / PR descriptions** carry value, contract, and the non-obvious decisions (§6). They do
  **not** re-describe the diff line by line — the diff is right there. A description longer than the
  change earns is a smell.
- **Review replies** add only what the reviewer cannot infer from the pushed diff: where you
  diverged from their suggestion and why, a behavior choice that is not self-evident, or an open
  question you need them to decide. Do not restate the changes they are about to read; when you did
  exactly what was asked, say so in a clause, not a recap.
- **No process scars.** No "Stage N", "as discussed", plan-step tags, or local commit churn in
  durable text (mirrors §6).

Distill to the irreducible signal: one tight clause beats a paragraph, and a long comment is a
prompt to compress or justify it, not to keep it.

**Gate:** fail if a comment, description, or reply restates the code/diff/issue it accompanies
instead of adding the context or rationale a reader lacks.

## Generalized adversarial failure patterns

Treat these as mandatory review prompts. Drop any row that is genuinely inapplicable to the
project, but justify the drop rather than skipping it silently.

| Failure pattern | Question to answer |
| --- | --- |
| Inert configuration | Which production consumer changes behavior because of this value? |
| Declaration/output mismatch | Where is the observed response validated against the declaration? |
| Dead accepted key | Can every documented-valid key reach a consumer? |
| Partial wiring | Do all entry points (CLI, server, API, plugin, direct construction) agree? |
| Compatibility invariant regression | Did a new field/value preserve positional construction, immutability, equality, hashing, serialization, and stable order? |
| Superclass/private mutation escape | Can a caller bypass nominal immutability through a base-class mutator or an assignable internal store? |
| Collection semantic mismatch | Do membership, equality, reflected equality, and hashing obey the advertised container contract? |
| Private representation leak | Does the documented snapshot API emit a plain public shape, is every persistence consumer using it, and can it be rehydrated? |
| Validator blindness | Can output derived only from the new input survive downstream validation? |
| Silent degradation | How does the user learn an input was ignored, skipped, or replaced by a stub/mock? |
| False hard bound | Are zero, tiny, exact-boundary, multiple-item, and truncation cases tested? |
| Budget starvation | Can optional work consume the budget required by correctness-critical work? |
| Non-monotonic priority | Can increasing a budget drop an earlier item while retaining a later one? |
| Cross-pool budget cliff | Can a newly admissible item in one reserved pool shrink content already retained in another? |
| Undocumented callback contract | Are callback input, output grammar, order, deduplication, and unresolved-item behavior explicit? |
| Contradictory rules | Does any older rule/config authorize what a new rule forbids? |
| Isolated happy-path tests | Is the interaction between the new features tested together, not just each alone? |
| Synthetic non-production fixture | Does the fixture use real shapes, distinct items, and realistic failure modes? |
| Cache/provenance gap | Do content changes invalidate reuse and appear in provenance where needed? |
| Undocumented stack | Can a maintainer review and merge this diff independently? |
| Documentation drift | Do repository, website, examples, and runtime behavior say the same thing? |
| Zero-delta prose | Does any comment, description, or reply restate the code, diff, or linked issue instead of adding why/context the reader lacks? |

### Additional prompts for LLM / prompt / context / RAG changes

Apply only when the change touches model prompts, retrieval, or grounding:

| Failure pattern | Question to answer |
| --- | --- |
| Prompt trust overclaim | Are delimiters described as framing, not sanitization? |
| Prefix/suffix grounding alias | Can a fabricated qualifier, owner, extension, or suffix borrow validity from a real path or symbol? |
| Incomplete path grammar | Are source, documentation, configured-evidence, and structured-context paths recognized consistently? |
| Ambiguous path/route grammar | Do positive repository-path and negative URL/domain/route/command cases share one explicit, tested classifier? |
| Classifier fallthrough | Can a token rejected as an external path fall through and be misclassified (e.g. as a symbol), especially after case changes? |
| Synthetic provenance inflation | Can a truncation marker or wrapper claim a source line/byte that was not included? |
| Marker-only evidence | Can framing or a truncation marker with zero retained source bytes count as evidence or activate a feature? |
| Normalized exact excerpt | Does line slicing preserve selected source content, including line endings and boundary whitespace, except documented framing/truncation? |
| New evidence class acceptance | For a new evidence class, does a response citing an identifier found only in that class survive downstream grounding, while a fabricated or wrong-owner identifier is rejected or demoted? Prompt-assembly assertions alone are insufficient. |

## Ready-for-review evidence record

Complete this table for every PR. Links may point to tests, issue comments, logs, CI runs, or
commit hashes. Every row requires either evidence or `N/A — reason`; rows marked **always** cannot
be N/A.

| Evidence | Applicability | Required result | Recorded result |
| --- | --- | --- | --- |
| Base revision | **Always** | Fetched base SHA and timestamp | |
| Related issue / maintainer alignment | Public feature/config: required; otherwise assess | Link, or `N/A — reason` where permitted | |
| User/maintainer scenario | **Always** | Concrete before and after | |
| Baseline evidence | **Always** | Applicable bug/feature/refactor/docs rule satisfied | |
| Acceptance proof | **Always** | Branch acceptance criteria pass | |
| Contract | Behavior/API/config changes | Defaults, precedence, invalid values, mismatch behavior | |
| Compatibility matrix | Behavior/API/config changes | Applicable entry points assessed and tested | |
| Failure observability | Fallible input/runtime changes | No silent skip or downgrade | |
| Security/trust | Input, prompt, path, subprocess, or network changes | Accurate limitations and hostile-input tests | |
| Cache/persistence | Cache/schema/stored/generated-output changes | Tested, or `N/A — reason` | |
| Focused tests | **Always** | Exact command and result | |
| Broad/full local suite | Assess | Exact result, or bounded-run limitation and focused attribution | |
| Format/lint | **Always for code changes** | Repository-wide pass, or changed-file pass plus recorded base debt | |
| Relevant integration/frontend tests | Assess | Exact commands/results, or `N/A — reason` | |
| Required CI | **Always before review request** | Required jobs and support matrix pass | |
| Project risk/impacted/health tooling | If the project ships it | Results and disposition | |
| Fresh adversarial review | **Always** | Zero P0/P1; P2 resolved or owner-accepted | |
| Documentation | User-visible behavior/config changes | Reference, examples, migration/reindex notes | |
| Stack/dependencies | Assess | Independently reviewable, or explicit parent and merge order | |

The human owner gives the final **READY FOR MAINTAINER REVIEW** decision. Before that decision, a
branch may be pushed to a personal fork and a draft PR opened solely for CI/collaboration, but
upstream maintainers must not be requested or notified for review. No branch is ready until its
evidence record is complete and a fresh adversarial rerun passes the gate.
