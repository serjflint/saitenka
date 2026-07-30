export const meta = {
  name: 'sharpen-loop',
  description: 'One-module Sharpen run: triage → measure → propose (≤3) → objective gate → isolated skeptic → judge → ledger/PR',
  whenToUse: 'Idle-time test-sharpening on the Saitenka overlay suite. One module per run; author≠skeptic isolation is structural. Set args.openPr=true to reach the human merge gate; default is a dry-run (ledger only).',
  phases: [
    { title: 'Select' },
    { title: 'Measure' },
    { title: 'Propose' },
    { title: 'Objective gate' },
    { title: 'Skeptic' },
    { title: 'Judge' },
    { title: 'Record' },
  ],
}

// Stage 4 of vibe/sharpen-loop-plan.md — the harness that makes a run non-`dry-run`. The deterministic
// judgment lives in the Python tools (sharpen_triage / sharpen_gate / sharpen_ledger); this script only
// orchestrates them and enforces the one thing a single context cannot self-provide: an adversarial
// review where the skeptic never sees the author's reasoning (.agents/sharpen/SPEC.md → Fidelity).
//
// The workflow runtime has no shell/fs, so every deterministic command runs *inside* an executor agent
// that returns schema-validated structured output — the tool is deterministic, the agent is the runner.
//
// args: { module?: string, openPr?: boolean (default false → dry-run), maxRetries?: number (default 3) }

const cfg = args || {}
const OPEN_PR = cfg.openPr === true
const MAX_RETRIES = Number.isInteger(cfg.maxRetries) ? cfg.maxRetries : 3
const CWD = 'overlay' // poe tasks + tools run from overlay/ (never the repo root)

// --- schemas ---------------------------------------------------------------------------------------

const SELECT = {
  type: 'object', additionalProperties: false,
  required: ['found', 'module', 'tests', 'status', 'reason'],
  properties: {
    found: { type: 'boolean', description: 'false when triage has no live candidate (all excluded/sharpened)' },
    module: { type: 'string', description: 'module key relative to src/overlay, e.g. app/scoring.py' },
    tests: { type: 'array', items: { type: 'string' }, description: 'mapped test file paths' },
    status: { type: 'string', description: 'ledger status of the pick (unseen/stale-sha/in-progress/...)' },
    survival: { type: ['number', 'null'], description: 'recorded non-equiv mutation survival, or null' },
    score: { type: 'number' },
    reason: { type: 'string', description: 'why this module (the composite components) or why none' },
  },
}

const BASELINE = {
  type: 'object', additionalProperties: false,
  required: ['green', 'quarantined', 'before'],
  properties: {
    green: { type: 'boolean', description: 'the in-scope suite is green & non-flaky before any edit' },
    quarantined: { type: 'array', items: { type: 'string' }, description: 'pre-red/flaky nodes excluded from the snapshot' },
    before: {
      type: 'object', additionalProperties: false,
      required: ['survival', 'conformance', 'actionable'],
      properties: {
        survival: { type: ['number', 'null'] },
        conformance: { type: 'integer' },
        actionable: { type: 'integer' },
        db: { type: ['string', 'null'], description: 'cosmic-ray session DB path from `poe mutate`, if run this pass' },
        survivor_func: { type: ['string', 'null'], description: 'the def carrying the non-equiv survivor cluster to target' },
      },
    },
  },
}

const PROPOSAL = {
  type: 'object', additionalProperties: false,
  required: ['applied', 'test_file', 'cut_module', 'touched_func', 'diff', 'proposals'],
  properties: {
    applied: { type: 'boolean', description: 'the edit was written to the test file' },
    test_file: { type: 'string', description: 'edited test path, repo-relative (e.g. overlay/tests/test_x.py)' },
    cut_module: { type: 'string', description: 'dotted code-under-test module for anti-cheat cut-derived, e.g. overlay.app.scoring' },
    touched_func: { type: 'string', description: 'the production def whose survivors this claims to kill (scopes the efficacy replay)' },
    diff: { type: 'string', description: 'unified diff of the test edit' },
    proposals: {
      type: 'array', minItems: 1,
      items: {
        type: 'object', additionalProperties: false,
        required: ['target_test', 'axis', 'change', 'rationale'],
        properties: {
          target_test: { type: 'string' }, axis: { type: 'string' },
          change: { type: 'string' }, rationale: { type: 'string' },
          claimed_kills: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const GATE = {
  type: 'object', additionalProperties: false,
  required: ['pass', 'anticheat_clean', 'efficacy_pass', 'report'],
  properties: {
    pass: { type: 'boolean', description: 'both arms clean' },
    anticheat_clean: { type: 'boolean' },
    efficacy_pass: { type: 'boolean' },
    report: { type: 'string', description: 'the exact bounce lines / earned+regressed counts, verbatim from the tool' },
  },
}

const REVIEW = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'grounds'],
  properties: {
    verdict: { type: 'string', enum: ['UPHELD', 'REFUTED'], description: 'REFUTED = drop the change' },
    grounds: { type: 'array', items: { type: 'string' }, description: 'evidence citing mutants/tests/diff, never authority' },
    constructed_bug: { type: ['string', 'null'], description: 'a concrete bug the edit still lets slip, or null' },
  },
}

const RECORD = {
  type: 'object', additionalProperties: false,
  required: ['state', 'ledger_appended'],
  properties: {
    state: { type: 'string', enum: ['sharpened', 'in-progress', 'blocked-on-bug', 'dry-run', 'left-undone'] },
    ledger_appended: { type: 'boolean' },
    pr_url: { type: ['string', 'null'] },
    grow_issues: { type: 'array', items: { type: 'string' } },
    note: { type: 'string' },
  },
}

// --- run -------------------------------------------------------------------------------------------

phase('Select')
const pick = await agent(
  `Run the Sharpen triage and return the top live candidate as structured output.\n` +
  `From ${CWD}/ run: \`uv run python tools/sharpen_triage.py --top 1\` (add --no-network only if gh is unauthenticated — it disables the open-PR exclusion, note that in reason).\n` +
  (cfg.module ? `The maintainer pinned module=${cfg.module}; use it ONLY if triage lists it as a live (non-excluded) candidate, else report found=false with why.\n` : '') +
  `The "→ pick:" line names the module; map it to its test files via the table / \`tools/sharpen_ledger.py\`. ` +
  `Never pick an EXCLUDED module (open-PR / sharpened-current / grow-filed). Return found=false if there is no live candidate.`,
  { phase: 'Select', schema: SELECT, label: 'triage' },
)

if (!pick || !pick.found) {
  log(`No live module to sharpen — ${pick ? pick.reason : 'triage failed'}`)
  return { done: false, reason: pick ? pick.reason : 'triage failed', openPr: OPEN_PR }
}
log(`pick: ${pick.module} (${pick.status}, score ${pick.score ?? '—'}) — ${pick.reason}`)

phase('Measure')
// Known-green baseline BEFORE any edit — a poisoned before/after bounces good work or hides a regression
// (SPEC step 2). Read structured tool output (session DB, test-lint --json), never scraped console.
const base = await agent(
  `Establish the known-green baseline + before-snapshot for module ${pick.module} (tests: ${pick.tests.join(', ')}), from ${CWD}/.\n` +
  `1. Run the mapped tests; any pre-red/flaky node is QUARANTINED (list it), never folded into a number.\n` +
  `2. Conformance: \`uv run poe test-lint-json\`, count this module's hits (total + actionable; the metric rules are test-assert-private-attr / test-monkeypatch-private-target).\n` +
  `3. Efficacy (pure-core modules only — skip for glue like controller/mpvio): \`uv run poe mutate ${pick.module.replace('app/', '').replace('.py', '')}\` builds the cosmic-ray session DB under $TMPDIR; report its path and the def name carrying the non-equivalent survivor cluster worth targeting.\n` +
  `Return the before-snapshot. green=false is a valid, honest outcome (record it and stop upstream).`,
  { phase: 'Measure', schema: BASELINE, label: 'baseline' },
)

if (!base || !base.green) {
  log(`Baseline not green — cannot measure an honest before/after. Quarantined: ${base ? base.quarantined.join(', ') : '?'}`)
  return { done: false, module: pick.module, reason: 'baseline not green', quarantined: base?.quarantined ?? [] }
}

phase('Propose')
// Author gets the MINIMUM decisive context (survivor coordinate, target test file, rubric) — never a
// whole-repo dump. Retries carry only the one high-value context: the prior attempt + why the gate bounced.
let proposal = null
let gate = null
let carry = ''
for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
  proposal = await agent(
    `You are the Sharpen AUTHOR for module ${pick.module}. Tighten the EXISTING tests (${pick.tests.join(', ')}) so they catch a real change they currently miss — do not write new-feature coverage (that is Grow's job; file an issue instead).\n` +
    `Minimum decisive context: the before-snapshot is ${JSON.stringify(base.before)}. The non-equivalent survivor cluster lives in \`${base.before.survivor_func ?? 'the module'}\`. Read only that function + the target test file + AGENTS.md "## Testing".\n` +
    `Assert OBSERVABLE behaviour (return value / emitted IPC / written note), never a private attr or mock call-count. Apply the edit to the test file. Emit a named, deduplicated proposal list and the unified diff. Set touched_func to the production def whose survivors you claim to kill, and cut_module to its dotted path.\n` +
    (carry ? `\nPRIOR ATTEMPT BOUNCED — do not repeat it. Gate report:\n${carry}\n` : ''),
    { phase: 'Propose', schema: PROPOSAL, label: `author#${attempt}` },
  )
  if (!proposal || !proposal.applied) { carry = 'author did not apply an edit'; continue }

  // Objective gate (deterministic, no judgment): anti-cheat static diff + efficacy mutation replay.
  gate = await agent(
    `Run the deterministic Sharpen objective gate on the author's edit, from ${CWD}/. Report the tool output VERBATIM — do not interpret.\n` +
    `Arm B (anti-cheat, fast): \`uv run python tools/sharpen_gate.py anticheat ${proposal.test_file.replace('overlay/', '')} --cut ${proposal.cut_module} --repo .\` (bounces removed/weakened/trivial/cut-derived asserts vs HEAD).\n` +
    (base.before.db
      ? `Arm A (efficacy replay, minutes): \`uv run --extra full --with cosmic-ray python tools/sharpen_gate.py efficacy --db ${base.before.db} --module src/${pick.module} --func ${proposal.touched_func} --tests ${pick.tests.map(t => t.replace('overlay/', '')).join(' ')} --repo .\` (earned kills + full-control no-regression).\n`
      : `Arm A (efficacy): SKIP — no session DB (glue module, mutation-excluded). Record efficacy_pass=true with report "n/a — glue".\n`) +
    `pass = both arms clean. If either bounces, pass=false and quote the BOUNCE/REGRESSED lines.`,
    { phase: 'Objective gate', schema: GATE, label: `gate#${attempt}`, effort: 'low' },
  )
  if (gate && gate.pass) break
  carry = gate ? gate.report : 'gate execution failed'
  log(`attempt ${attempt} bounced: ${carry.split('\n')[0]}`)
  // revert the failed edit so the next author starts from a known-green tree
  await agent(
    `From ${CWD}/ run \`git checkout -- ${proposal.test_file.replace('overlay/', '')}\` to discard the bounced edit. Confirm the tree is clean.`,
    { phase: 'Propose', label: `revert#${attempt}`, effort: 'low' },
  )
  proposal = null
}

if (!proposal || !gate || !gate.pass) {
  // Terminal: un-sharpenable within the retry cap. Not a spin — record and stop (SPEC step 3).
  const rec = await recordOutcome('left-undone', null, null, null,
    `No proposal cleared the objective gate in ${MAX_RETRIES} attempts. Last bounce: ${carry}`)
  return { done: true, module: pick.module, state: rec?.state ?? 'left-undone', openPr: OPEN_PR }
}

phase('Skeptic')
// ISOLATED refutation — the skeptic sees only what/why/diff, NEVER the author's reasoning (a separate
// agent() call = harness-enforced context isolation). Framed as adversarial ("construct a bug this
// misses; default REFUTED on doubt") and grounded in the artifact, not the author's rationale (SycEval).
const skeptic = await agent(
  `You are an adversarial reviewer. A test edit to ${pick.module} claims to make the suite catch a real change it currently misses. Your job: try to REFUTE it. Reason ONLY from the artifact below and the code — you are NOT given the author's reasoning.\n\n` +
  `WHAT (proposals): ${JSON.stringify(proposal.proposals)}\n\nDIFF:\n${proposal.diff}\n\n` +
  `Read the touched production function \`${proposal.touched_func}\` and the edited test. Construct a concrete bug the edit STILL lets slip, or a way it merely pins an implementation detail / derives its expected value from the code under test / adds nothing over what was already asserted. If you find one, verdict=REFUTED. Cite mutants/tests/lines as grounds — never authority. Default REFUTED on genuine doubt.`,
  { phase: 'Skeptic', schema: REVIEW, label: 'skeptic' },
)

let verdict = skeptic?.verdict ?? 'REFUTED'
let judgeNote = 'skeptic decisive'
if (verdict === 'REFUTED') {
  phase('Judge')
  // Disagreement (objective gate passed, skeptic refuted) → a Sonnet judge on the framed dispute.
  // Default on genuine controversy is DROP (iterative debate-to-consensus is the sycophancy trap).
  const judge = await agent(
    `Adjudicate a Sharpen dispute. The deterministic objective gate PASSED (anti-cheat + mutation replay: ${gate.report}). An isolated skeptic voted REFUTED on these grounds: ${JSON.stringify(skeptic.grounds)} (constructed bug: ${skeptic.constructed_bug ?? 'none'}).\n` +
    `The edit: ${JSON.stringify(proposal.proposals)}\nDIFF:\n${proposal.diff}\n\n` +
    `Is the skeptic's objection substantive (a real over-fit / zero-value / change-detector edit) or not? Verdict UPHELD only if the objection is clearly unfounded AND the edit plainly improves bug-catching. On genuine controversy, verdict=REFUTED (drop).`,
    { phase: 'Judge', schema: REVIEW, label: 'judge', model: 'sonnet' },
  )
  verdict = judge?.verdict === 'UPHELD' ? 'UPHELD' : 'REFUTED'
  judgeNote = `judge (sonnet): ${judge?.verdict ?? 'REFUTED (default)'}`
}

phase('Record')
if (verdict !== 'UPHELD') {
  await agent(
    `From ${CWD}/ run \`git checkout -- ${proposal.test_file.replace('overlay/', '')}\` — the review dropped the edit. Confirm clean tree.`,
    { phase: 'Record', label: 'revert-dropped', effort: 'low' },
  )
  const rec = await recordOutcome('dry-run', null, skeptic, judgeNote,
    `Review dropped the change (${judgeNote}). Grounds: ${JSON.stringify(skeptic?.grounds ?? [])}`)
  return { done: true, module: pick.module, state: 'dry-run', verdict, openPr: OPEN_PR }
}

const rec = await recordOutcome(OPEN_PR ? 'in-progress' : 'dry-run', proposal, skeptic, judgeNote, null)
return { done: true, module: pick.module, state: rec?.state ?? (OPEN_PR ? 'in-progress' : 'dry-run'), pr: rec?.pr_url ?? null, openPr: OPEN_PR }

// --- record helper ---------------------------------------------------------------------------------

async function recordOutcome(state, prop, skepticReview, judgeNote, extraNote) {
  // A shippable state (in-progress/sharpened) requires OPEN_PR: a valid review block + a real PR. Without
  // it we record dry-run (SPEC → Fidelity: no fidelity ⇒ no ship). The executor agent has the shell to
  // stamp the real date and hash — the JS runtime can't (no Date.now / fs).
  const review = skepticReview
    ? `{ "author": "sharpen-author (workflow-isolated)", "skeptic": "sharpen-skeptic (workflow-isolated, ≠author)", "judge": "${judgeNote}", "verdict": "${skepticReview.verdict}" }`
    : 'null (terminal outcome, no review reached)'
  const wantPr = OPEN_PR && state === 'in-progress' && prop
  return agent(
    `Append one Sharpen ledger record and ${wantPr ? 'open the PR' : 'stop at the ledger (dry-run: no PR, no outward action)'}, from the repo root (parent of ${CWD}/).\n` +
    `Module ${pick.module}. Compute source_sha with tools/sharpen_ledger.py, stamp \`audited\` from \`date -u +%Y-%m-%dT%H:%M:%SZ\`, toolset_version from the ledger manifest.\n` +
    `state: "${state}". review block: ${review}.\n` +
    (prop ? `decisions: ${JSON.stringify(prop.proposals.map(p => p.change))}. axes before: ${JSON.stringify(base.before)}.\n` : '') +
    `axes_not_applied: list every axis you skipped and WHY (the guard against silent no-run — SPEC Self-reflection).\n` +
    (extraNote ? `note: ${extraNote}\n` : '') +
    (wantPr
      ? `Then open a PR on a fresh branch off main (feat/sharpen-<module-stem>): body per SPEC "PR body" — the WHAT (diff) + WHY a human should care in project terms, the four-axis before/after, the disposition (coverage-only / issue-filed / fix-included), and gate report ${JSON.stringify(gate.report)}. Return its url. Do NOT merge.\n`
      : `Do NOT open a PR, push, or file any issue. Leave the ledger append as the only change; print the PR body that WOULD be opened so the maintainer can review the run before enabling openPr.\n`),
    { phase: 'Record', schema: RECORD, label: 'record' },
  )
}
