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
    { title: 'Ship gate' },
    { title: 'Reflect' },
    { title: 'Record' },
  ],
}

// Claude Workflow adapter for the provider-neutral contract in ADAPTERS.md. The deterministic
// judgment lives in the Python tools (sharpen_triage / sharpen_gate / sharpen_ledger); this script only
// orchestrates them and enforces the one thing a single context cannot self-provide: an adversarial
// review where the skeptic never sees the author's reasoning (.agents/sharpen/SPEC.md → Fidelity).
//
// The workflow runtime has no shell/fs, so every deterministic command runs *inside* an executor agent
// that returns schema-validated structured output — the tool is deterministic, the agent is the runner.
//
// args: { module?: string, openPr?: boolean (default false → dry-run), maxRetries?: number (default 3) }

const cfg = args || {}
const CONTRACT_VERSION = 6 // mirrors contracts.json; the Workflow runtime cannot read local files
const OPEN_PR = cfg.openPr === true
const MAX_RETRIES = Number.isInteger(cfg.maxRetries) ? cfg.maxRetries : 3
const CWD = '.' // poe tasks + tools run from the launch worktree root

// BEGIN GENERATED SHARPEN POLICY — tools/sharpen_policy.py sync
const SHARPEN_POLICY = {"axes":["efficacy","conformance","preservation","brittleness","redundancy"],"gate_not_false":["preservation_pass"],"modes":{"conformance":{"gate_null":["efficacy_pass"],"gate_true":["pass","anticheat_clean","conformance_pass","restoration_verified"],"primary_axis":"conformance"},"efficacy":{"gate_null":["conformance_pass"],"gate_true":["pass","anticheat_clean","efficacy_pass","restoration_verified"],"primary_axis":"efficacy"}},"optional_passing_axes":["preservation","brittleness"],"primary_axes":["efficacy","conformance"],"version":1}

const objectiveGatePassed = (candidate, efficacyMode) => {
  const mode = SHARPEN_POLICY.modes[efficacyMode ? 'efficacy' : 'conformance']
  const required = [...mode.gate_true, ...mode.gate_null, ...SHARPEN_POLICY.gate_not_false]
  return required.every((field) => Object.hasOwn(candidate ?? {}, field)) &&
    mode.gate_true.every((field) => candidate[field] === true) &&
    mode.gate_null.every((field) => candidate?.[field] === null) &&
    SHARPEN_POLICY.gate_not_false.every((field) => candidate[field] === true || candidate[field] === null)
}
// END GENERATED SHARPEN POLICY

// Worktree-safe: launch this run from a dedicated git worktree (EnterWorktree → Workflow → ExitWorktree)
// so executor edits can't touch the maintainer's live tree. Every executor therefore operates on paths
// RELATIVE to its inherited cwd — an absolute `cd /Users/.../saitenka` would escape the worktree.
const REL = 'Run from the worktree root relative to your current working directory. Do NOT use ' +
  'absolute paths or `cd` outside the repo you were launched in — this run may be inside a git worktree.'

// Hard scope guard — the 2026-07-30 first run edited a tracked tool to work around a blocker. Never again.
const GUARD = 'SCOPE: edit ONLY the one target test file named below. Never edit any source/tool/config ' +
  'file, never add a mutation target, never install anything. If something blocks you, STOP and return ' +
  'the blocker verbatim — do not work around it by touching another file. The author does not run tests ' +
  'or gates; the root executor owns authoritative execution.'

// --- schemas ---------------------------------------------------------------------------------------

const SELECT = {
  type: 'object', additionalProperties: false,
  required: ['found', 'module', 'tests', 'status', 'outer_reflection_due', 'reason'],
  properties: {
    found: { type: 'boolean', description: 'false when triage has no live candidate (all excluded/sharpened)' },
    module: { type: 'string', description: 'module key relative to src/saitenka, e.g. app/scoring.py' },
    tests: { type: 'array', items: { type: 'string' }, description: 'mapped test file paths' },
    status: { type: 'string', description: 'ledger status of the pick (unseen/stale-sha/in-progress/...)' },
    survival: { type: ['number', 'null'], description: 'recorded non-equiv mutation survival, or null' },
    score: { type: 'number' },
    conformance: { type: 'integer', description: 'triage `conf=` column (total test-lint hits) — deterministic, do not recompute later' },
    actionable: { type: 'integer', description: 'triage `act=` column (per-hit-fixable violations) — deterministic, do not recompute later' },
    pr_exclusion_checked: { type: 'boolean', description: 'true ONLY if triage ran the open-PR exclusion (gh authenticated, no --no-network); false if it could not — the harness then refuses to open a PR (fail-closed)' },
    outer_reflection_due: { type: 'boolean', description: 'exactly the due value from sharpen_ledger.py reflection-status' },
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
  required: ['applied', 'test_file', 'cut_module', 'touched_func', 'preservation_required', 'witness_find', 'witness_replace', 'diff', 'proposals'],
  properties: {
    applied: { type: 'boolean', description: 'the edit was written to the test file' },
    test_file: { type: 'string', description: 'edited test path, repo-relative (e.g. tests/test_x.py)' },
    cut_module: { type: 'string', description: 'dotted code-under-test module for anti-cheat cut-derived, e.g. saitenka.app.scoring' },
    touched_func: { type: 'string', description: 'the production def whose survivors this claims to kill (scopes the efficacy replay)' },
    preservation_required: { type: 'boolean', description: 'existing assertion removed/changed without a campaign' },
    witness_find: { type: ['string', 'null'], description: 'exact source snippet for the old kill witness' },
    witness_replace: { type: ['string', 'null'], description: 'scenario-breaking witness replacement' },
    diff: { type: 'string', description: 'unified diff of the test edit' },
    reason: { type: ['string', 'null'], description: 'why no edit was applied' },
    proposals: {
      type: 'array',
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
  required: ['pass', 'anticheat_clean', 'efficacy_pass', 'conformance_pass', 'preservation_pass', 'restoration_verified', 'report'],
  properties: {
    pass: { type: 'boolean', description: 'both arms clean' },
    anticheat_clean: { type: 'boolean' },
    efficacy_pass: { type: ['boolean', 'null'] },
    conformance_pass: { type: ['boolean', 'null'] },
    preservation_pass: { type: ['boolean', 'null'] },
    restoration_verified: { type: 'boolean' },
    report: { type: 'string', description: 'the exact bounce lines / earned+regressed counts, verbatim from the tool' },
  },
}

const REVIEW = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'grounds', 'constructed_bug', 'better_fix'],
  properties: {
    verdict: { type: 'string', enum: ['UPHELD', 'REFUTED'], description: 'REFUTED = drop the change' },
    grounds: { type: 'array', items: { type: 'string' }, description: 'evidence citing mutants/tests/diff, never authority' },
    constructed_bug: { type: ['string', 'null'], description: 'a concrete bug the edit still lets slip, or null' },
    better_fix: {
      type: ['object', 'null'], additionalProperties: false,
      required: ['summary', 'scope', 'evidence'],
      properties: {
        summary: { type: 'string' },
        scope: { type: 'string', enum: ['target-test', 'outside-sharpen'] },
        evidence: { type: 'string' },
      },
      description: 'smallest alternative when the objective is valid but this candidate is the wrong intervention; null otherwise',
    },
  },
}

const SHIP_GATE = {
  type: 'object', additionalProperties: false,
  required: ['pass', 'report'],
  properties: {
    pass: { type: 'boolean' },
    report: { type: 'string', description: 'poe all exit status and concise failure detail' },
  },
}

const RECORD = {
  type: 'object', additionalProperties: false,
  required: ['state', 'ledger_appended', 'recorded_module', 'recorded_source_sha', 'recorded_toolset_version', 'recorded_contract_version', 'recorded_axes_not_applied'],
  properties: {
    state: { type: 'string', enum: ['sharpened', 'in-progress', 'blocked-on-bug', 'dry-run', 'left-undone'] },
    ledger_appended: { type: 'boolean' },
    recorded_module: { type: 'string' },
    recorded_source_sha: { type: 'string', pattern: '^[0-9a-f]{64}$' },
    recorded_toolset_version: { type: 'integer', minimum: 1 },
    recorded_contract_version: { type: 'integer' },
    recorded_axes_not_applied: { type: 'boolean' },
    pr_url: { type: ['string', 'null'] },
    grow_issues: { type: 'array', items: { type: 'string' } },
    note: { type: 'string' },
  },
}

const OUTWARD = {
  type: 'object', additionalProperties: false,
  required: ['pr_url'],
  properties: { pr_url: { type: ['string', 'null'] } },
}

// --- run -------------------------------------------------------------------------------------------

phase('Select')
const pick = await agent(
  `Run the Sharpen triage and return the top live candidate as structured output.\n` +
  `From ${CWD}/ first run \`uv run python tools/sharpen_ledger.py reflection-status\`; copy its due value exactly to outer_reflection_due. Then run \`uv run python tools/sharpen_triage.py --top 1\`. Set pr_exclusion_checked=true ONLY if it ran with the open-PR exclusion active (gh authenticated, NO --no-network). If gh is unauthenticated you may add --no-network, but then set pr_exclusion_checked=false — the harness will refuse to open a PR (it can't confirm the module has no in-flight PR).\n` +
  (cfg.module ? `The maintainer pinned module=${cfg.module}; use it ONLY if triage lists it as a live (non-excluded) candidate, else report found=false with why.\n` : '') +
  `The "→ pick:" line names the module; map it to its test files via the table / \`tools/sharpen_ledger.py\`. ` +
  `Copy the pick's \`conf=\` and \`act=\` columns verbatim into conformance/actionable — these are the deterministic counts the rest of the loop uses; do NOT recompute them anywhere else. ` +
  `Never pick an EXCLUDED module (open-PR / sharpened-current / grow-filed). Return found=false if there is no live candidate.`,
  { phase: 'Select', schema: SELECT, label: 'triage' },
)

if (pick?.outer_reflection_due === true) {
  phase('Reflect')
  log('Periodic Sharpen toolset reflection is due; no module audit may start')
  return { done: false, reason: 'outer-reflection-due', openPr: OPEN_PR }
}

if (!pick || !pick.found) {
  log(`No live module to sharpen — ${pick ? pick.reason : 'triage failed'}`)
  return { done: false, reason: pick ? pick.reason : 'triage failed', openPr: OPEN_PR }
}
log(`pick: ${pick.module} (${pick.status}, score ${pick.score ?? '—'}) — ${pick.reason}`)

// Fail-closed: only open a PR if the open-PR exclusion actually ran (SPEC → never sharpen a module with
// an open feature branch). If triage couldn't check it (gh unauth / --no-network), force a dry-run so the
// loop can't fight in-flight work — a structural guard, not a prompt-dependent one.
const canOpenPr = OPEN_PR && pick.pr_exclusion_checked === true
if (OPEN_PR && !canOpenPr) {
  log(`open-PR exclusion unverified (gh unauth / --no-network) — forcing dry-run: cannot confirm ${pick.module} has no in-flight PR`)
}

phase('Measure')
// Known-green baseline BEFORE any edit — a poisoned before/after bounces good work or hides a regression
// (SPEC step 2). Read structured tool output (session DB, test-lint --json), never scraped console.
// Efficacy CONSUMES a pre-built mutation campaign; it never launches one (a campaign outlives a 10-min
// step budget — that killed the first run). The campaign is a slow out-of-band pre-req.
const DB_REL = `.mutation-cache/${`src/saitenka/${pick.module}`.replaceAll('/', '_')}.sqlite` // matches run.py db_path(), relative to repository root
const TARGET_KEY = pick.module.split('/').pop().replace('.py', '') // allowlist key = module basename
const base = await agent(
  `Establish the known-green baseline + before-snapshot for module ${pick.module} (tests: ${pick.tests.join(', ')}). ${REL} ${GUARD} (Measure runs read-only tools — no edits at all.)\n` +
  `1. Run the mapped tests; any pre-red/flaky node is QUARANTINED (list it), never folded into a number.\n` +
  `2. Conformance counts are ALREADY computed by triage (conformance=${pick.conformance ?? '?'}, actionable=${pick.actionable ?? '?'}); echo those into before.conformance/before.actionable — do NOT recompute (recomputing here undercounted on the 2026-07-30 run).\n` +
  `3. Efficacy — do NOT run \`poe mutate\`. Gate on the allowlist: \`uv run poe mutate --list\`. If \`${TARGET_KEY}\` is NOT listed → glue, set before.db=null, before.survival=null (Conformance-driven run). If it IS listed, look for a COMPLETE campaign DB at "${DB_REL}": if present, read the survival rate (\`cr-rate "${DB_REL}"\`) and the def carrying the most SURVIVED mutants (\`cosmic-ray dump\` grouped by definition_name) → set before.db to that path + before.survivor_func. If the DB is absent/partial → DEFER Efficacy: before.db=null, and note "no complete campaign DB — run \`poe mutate ${TARGET_KEY}\` out-of-band, Efficacy deferred this run".\n` +
  `Return the before-snapshot. green=false is a valid, honest outcome (record it and stop upstream).`,
  { phase: 'Measure', schema: BASELINE, label: 'baseline' },
)

if (!base || !base.green) {
  log(`Baseline not green — cannot measure an honest before/after. Quarantined: ${base ? base.quarantined.join(', ') : '?'}`)
  const quarantined = base?.quarantined ?? []
  const rec = await recordOutcome('dry-run', null, null,
    `Known-green baseline unavailable; quarantined: ${quarantined.join(', ') || 'baseline executor failed'}. No edit attempted.`)
  return { done: true, module: pick.module, state: rec?.state ?? 'dry-run', reason: 'baseline not green', quarantined }
}
// Triage's counts are the deterministic source of truth — override whatever Measure echoed (defends
// against the recompute-undercount bug even if the agent ignores the instruction above).
base.before.conformance = pick.conformance ?? base.before.conformance ?? 0
base.before.actionable = pick.actionable ?? base.before.actionable ?? 0

phase('Propose')
// Author gets the MINIMUM decisive context (survivor coordinate, target test file, rubric) — never a
// whole-repo dump. Retries carry only the one high-value context: the prior attempt + why the gate bounced.
// Two modes by what Measure found: a live campaign DB ⇒ Efficacy (kill survivors); else ⇒ Conformance
// (fix the actionable test-lint violation). Efficacy is the differentiator; Conformance is the workhorse.
const efficacyMode = !!base.before.db
let proposal = null
let gate = null
let carry = ''
let authorInvocation = null

// Nothing to sharpen — no survivor to kill AND no actionable conformance violation. Don't spin the
// author into fabricating a cosmetic, zero-value edit (the 2026-07-30 run split `assert A and B` into
// two asserts; the review correctly dropped it, but the cycle was wasted). Record and stop.
if (!efficacyMode && base.before.actionable === 0) {
  log(`Nothing to sharpen on ${pick.module}: 0 actionable conformance violations, no mutation survivor.`)
  const rec = await recordOutcome('left-undone', null, null,
    `Nothing to sharpen: ${base.before.conformance} conformance hits are all metric (architecture/Grow signal, no per-hit fix), no mutation survivor. Efficacy ${base.before.db ? 'clean' : 'deferred — no campaign DB'}.`)
  return { done: true, module: pick.module, state: rec?.state ?? 'left-undone', reason: 'nothing-to-sharpen', openPr: OPEN_PR }
}

for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
  proposal = await agent(
    `You are the Sharpen AUTHOR for module ${pick.module}. Tighten the EXISTING tests (${pick.tests.join(', ')}) so they catch a real change they currently miss — do not write new-feature coverage (that is Grow's job; file an issue instead). ${REL} ${GUARD}\n` +
    (efficacyMode
      ? `MODE Efficacy: kill the non-equivalent survivor cluster in \`${base.before.survivor_func ?? 'the module'}\`. Read only that function + the target test file + AGENTS.md "## Testing". Set touched_func to that def and cut_module to its dotted path.\n`
      : `MODE Conformance (${base.before.actionable} actionable violation(s) exist per triage): run \`uv run python tools/test_lint.py ${pick.tests.map((test) => `--file ${test}`).join(' ')}\` and inspect the actual hits — rules OTHER than metric/advisory ones (test-assert-private-attr / test-monkeypatch-private-target / test-sleep-polling). Fix ONE genuine hit: correct a mis-levelled tier marker, drop a redundant private half of a compound assert, or tighten an under-assertion to observable behaviour. There is no mutation survivor — do NOT invent one, and do NOT make a cosmetic edit that changes no caught-failure set (splitting \`assert A and B\` into two asserts is worthless — the review WILL drop it). If you cannot tie your edit to a concrete actionable test-lint hit, return applied=false. Set touched_func to "" and cut_module to the dotted module path.\n`) +
    `Minimum decisive context: the before-snapshot is ${JSON.stringify(base.before)}.\n` +
    `Assert OBSERVABLE behaviour (return value / emitted IPC / written note), never a private attr or mock call-count. Apply the edit ONLY to a target test file. If an existing assertion is removed or changed and there is no campaign, set preservation_required=true and supply a one-line witness_find→witness_replace source mutation that the old test killed and the proposal must still kill. Emit a named, deduplicated proposal list and the unified diff. If there is genuinely nothing worth sharpening, return applied=false with the reason.\n` +
    (carry ? `\nPRIOR ATTEMPT BOUNCED — do not repeat it. Gate report:\n${carry}\n` : ''),
    { phase: 'Propose', schema: PROPOSAL, label: `author#${attempt}` },
  )
  if (!proposal || !proposal.applied) { carry = 'author did not apply an edit'; continue }
  authorInvocation = `author#${attempt}` // adapter-assigned id; each agent() call is a fresh context

  // Objective gate (deterministic, no judgment): anti-cheat static diff + efficacy mutation replay.
  gate = await agent(
    `Run the deterministic Sharpen objective gate on the author's edit. ${REL} Report the tool output VERBATIM — do not interpret. (This step runs read-only tools; it must not edit any file.)\n` +
    `Arm B (anti-cheat, fast): \`uv run python tools/sharpen_gate.py anticheat ${proposal.test_file.replace('overlay/', '')} --cut ${proposal.cut_module} --repo .\` (bounces removed/weakened/trivial/cut-derived asserts vs HEAD).\n` +
    (base.before.db
      ? `Arm A (efficacy replay, minutes): \`uv run --extra full --with cosmic-ray python tools/sharpen_gate.py efficacy --db ${base.before.db} --module src/saitenka/${pick.module} --func ${proposal.touched_func} --tests ${pick.tests.map(t => t.replace('overlay/', '')).join(' ')} --repo .\` (earned kills + full-control no-regression). Set efficacy_pass from its verdict and conformance_pass=null (n/a).\n`
      : proposal.preservation_required
        ? `Arm A substitute (preservation witness): \`uv run python tools/sharpen_gate.py preserve --module src/saitenka/${pick.module} --find ${JSON.stringify(proposal.witness_find)} --replace ${JSON.stringify(proposal.witness_replace)} --test-file ${proposal.test_file.replace('overlay/', '')} --tests ${pick.tests.map(t => t.replace('overlay/', '')).join(' ')} --repo .\`. Set preservation_pass from its verdict.\n`
        : `Arm A/preservation: N/A — no campaign and no existing assertion changed. Set preservation_pass=null.\n`) +
    (!base.before.db
      ? `Conformance: \`uv run python tools/sharpen_gate.py conformance --module ${pick.module} --before-actionable ${base.before.actionable} --repo .\`. Set conformance_pass from its verdict and efficacy_pass=null (n/a).\n`
      : '') +
    `Verify source and test bytes were restored exactly and set restoration_verified. pass = anticheat_clean AND the active primary-axis verdict AND restoration_verified AND (preservation_pass !== false). Quote every BOUNCE/REGRESSED line.`,
    { phase: 'Objective gate', schema: GATE, label: `gate#${attempt}`, effort: 'low' },
  )
  if (objectiveGatePassed(gate, efficacyMode)) break
  carry = gate ? gate.report : 'gate execution failed'
  log(`attempt ${attempt} bounced: ${carry.split('\n')[0]}`)
  // revert the failed edit so the next author starts from a known-green tree
  await agent(
    `${REL} Run \`git checkout -- ${proposal.test_file.replace('overlay/', '')}\` to discard the bounced edit. Confirm the tree is clean.`,
    { phase: 'Propose', label: `revert#${attempt}`, effort: 'low' },
  )
  proposal = null
}

if (!proposal || !objectiveGatePassed(gate, efficacyMode)) {
  // Terminal: un-sharpenable within the retry cap. Not a spin — record and stop (SPEC step 3).
  const rec = await recordOutcome('left-undone', null, null,
    `No proposal cleared the objective gate in ${MAX_RETRIES} attempts. Last bounce: ${carry}`)
  return { done: true, module: pick.module, state: rec?.state ?? 'left-undone', openPr: OPEN_PR }
}

phase('Skeptic')
// ISOLATED refutation — the skeptic sees only factual WHAT + DIFF, NEVER the author's reasoning (a separate
// agent() call = harness-enforced context isolation). Framed as adversarial ("construct a bug this
// misses; default REFUTED on doubt") and grounded in the artifact, not the author's rationale (SycEval).
const skeptic = await agent(
  `You are an adversarial reviewer. A test edit to ${pick.module} claims to make the suite catch a real change it currently misses. Your job: try to REFUTE it. Reason ONLY from the artifact below and the code — you are NOT given the author's reasoning.\n\n` +
  // SycEval (SPEC → Fidelity): the author's persuasive rationale/claimed-kills must NOT reach the skeptic
  // — preemptive, authority-flavoured framing *increases* regressive agreement. Forward only the factual
  // what (target/axis/change) + the diff; the skeptic reasons from the code, not the author's "why".
  `WHAT (proposals): ${JSON.stringify(proposal.proposals.map((p) => ({ target_test: p.target_test, axis: p.axis, change: p.change })))}\n\nDIFF:\n${proposal.diff}\n\n` +
  `${REL} Read the touched production function \`${proposal.touched_func || '(conformance edit — no target function)'}\` and the edited test. Construct a concrete bug the edit STILL lets slip, or a way it merely pins an implementation detail / derives its expected value from the code under test / adds nothing over what was already asserted. If you find one, verdict=REFUTED. If the objective remains valid but this edit is too local or the wrong intervention, return the smallest evidence-backed better_fix and classify its scope; otherwise better_fix=null. A better fix never rescues this candidate. Cite mutants/tests/lines as grounds — never authority. Default REFUTED on genuine doubt.`,
  { phase: 'Skeptic', schema: REVIEW, label: 'skeptic' },
)
const skepticInvocation = 'skeptic'

// The author is NOT an independent reviewer — it wrote the edit. So a lone skeptic UPHELD is the only
// gate before the human, and a single sycophantic skeptic could ship a bad edit. Require TWO independent
// UPHOLDs to ship: the skeptic AND a second, independently-isolated reviewer (the judge). Either REFUTE →
// DROP. A skeptic REFUTED drops immediately (default-drop; no judge "rescue" — the one independent voice
// found a problem, and expected yield is low anyway).
let verdict = 'REFUTED'
let judgeNote = 'skeptic REFUTED — dropped'
let judge = null
let judgeInvocation = null
if (skeptic?.verdict === 'UPHELD') {
  phase('Judge')
  // A SECOND independent adversarial review — same isolation as the skeptic (what/diff only, no author
  // rationale, and NOT told the first skeptic's grounds, so its vote is genuinely independent).
  // The adapter chooses a verification-capable model; shared policy never names a provider model.
  judge = await agent(
    `You are a SECOND, independent adversarial reviewer (the first reviewer is not shown to you). A test edit to ${pick.module} claims to make the suite catch a real change it currently misses. Try to REFUTE it, reasoning ONLY from the artifact and the code.\n\n` +
    `WHAT (proposals): ${JSON.stringify(proposal.proposals.map((p) => ({ target_test: p.target_test, axis: p.axis, change: p.change })))}\n\nDIFF:\n${proposal.diff}\n\n` +
    `${REL} Read the touched production function \`${proposal.touched_func || '(conformance edit — no target function)'}\` and the edited test. Construct a concrete bug the edit STILL lets slip, or a way it merely pins an implementation detail / derives its expected value from the code under test / adds nothing. If you find one, verdict=REFUTED. If the objective remains valid but this edit is too local or the wrong intervention, return the smallest evidence-backed better_fix and classify its scope; otherwise better_fix=null. A better fix never rescues this candidate. Cite mutants/tests/lines, never authority. Default REFUTED on genuine doubt.`,
    { phase: 'Judge', schema: REVIEW, label: 'judge' },
  )
  judgeInvocation = 'judge'
  // Ship iff BOTH independent reviewers upheld.
  verdict = judge?.verdict === 'UPHELD' ? 'UPHELD' : 'REFUTED'
  judgeNote = `2nd independent review: ${judge?.verdict ?? 'REFUTED (default)'}`
}

const review = {
  author: authorInvocation,
  skeptic: skepticInvocation,
  judge: judgeInvocation,
  skeptic_verdict: skeptic?.verdict ?? 'REFUTED',
  judge_verdict: judge?.verdict ?? null,
  verdict,
}

phase('Record')
if (verdict !== 'UPHELD') {
  const refuter = skeptic?.verdict === 'REFUTED' ? skeptic : judge
  const betterFix = refuter?.better_fix ?? null
  await agent(
    `${REL} Run \`git checkout -- ${proposal.test_file.replace('overlay/', '')}\` — the review dropped the edit. Confirm clean tree.`,
    { phase: 'Record', label: 'revert-dropped', effort: 'low' },
  )
  const rec = await recordOutcome('dry-run', null, review,
    `Review dropped the change (${judgeNote}). Grounds: ${JSON.stringify(refuter?.grounds ?? [])}. ` +
    `Better fix hand-off (separate authorization required; never applied by this run): ${JSON.stringify(betterFix)}`)
  return { done: true, module: pick.module, state: 'dry-run', verdict, openPr: OPEN_PR }
}

if (canOpenPr) {
  phase('Ship gate')
  const shipGate = await agent(
    `Run the deterministic pre-push gate from ${CWD}/: \`uv run poe all\`. ${REL} Edit nothing. ` +
    `Set pass=true only on exit 0 and return a concise report with the failing task/output when red.`,
    { phase: 'Ship gate', schema: SHIP_GATE, label: 'ship-gate', effort: 'low' },
  )
  if (!shipGate?.pass) {
    await agent(
      `${REL} Run \`git checkout -- ${proposal.test_file.replace('overlay/', '')}\` — the post-review ship gate failed. Confirm clean tree.`,
      { phase: 'Ship gate', label: 'revert-ship-gate', effort: 'low' },
    )
    const rec = await recordOutcome('dry-run', null, review,
      `Post-review ship gate failed; no PR opened. ${shipGate?.report ?? 'No report returned.'}`)
    return { done: true, module: pick.module, state: rec?.state ?? 'dry-run', verdict, openPr: OPEN_PR }
  }
}

if (!canOpenPr) {
  // Hash the final pristine tree in the ledger: capture the diff in `proposal`, then revert BEFORE
  // recording. Recording first would make source_sha stale immediately after the revert.
  await agent(
    `${REL} Run \`git checkout -- ${proposal.test_file.replace('overlay/', '')}\` — this UPHELD dry-run keeps only the ledger record; discard the edit. Confirm clean tree.`,
    { phase: 'Record', label: 'revert-dryrun', effort: 'low' },
  )
}
const rec = await recordOutcome(canOpenPr ? 'in-progress' : 'dry-run', proposal, review, null)
return { done: true, module: pick.module, state: rec?.state ?? (canOpenPr ? 'in-progress' : 'dry-run'), pr: rec?.pr_url ?? null, openPr: OPEN_PR }

// --- record helper ---------------------------------------------------------------------------------

async function recordOutcome(state, prop, reviewResult, extraNote) {
  // A shippable state (in-progress/sharpened) requires canOpenPr: a valid review block + a real PR. Without
  // it we record dry-run (SPEC → Fidelity: no fidelity ⇒ no ship). The executor agent has the shell to
  // stamp the real date and hash — the JS runtime can't (no Date.now / fs).
  const reviewBlock = reviewResult
    ? JSON.stringify(reviewResult)
    : 'null (terminal outcome, no review reached)'
  const wantPr = canOpenPr && state === 'in-progress' && prop
  const axes = prop ? {
    ...(gate?.efficacy_pass == null ? {} : {
      efficacy: {
        status: gate.efficacy_pass ? 'pass' : 'fail', evidence: gate.report,
        detail: { before: base.before.survival, after: null },
      },
    }),
    ...(gate?.conformance_pass == null ? {} : {
      conformance: {
        status: gate.conformance_pass ? 'pass' : 'fail', evidence: gate.report,
        detail: { before: base.before, decisions: prop.proposals.map(p => p.change), diff: prop.diff },
      },
    }),
    ...(gate?.preservation_pass == null ? {} : {
      preservation: { status: gate.preservation_pass ? 'pass' : 'fail', evidence: gate.report },
    }),
  } : {}
  const axesNotApplied = prop ? [
    ...(gate?.efficacy_pass == null ? ['efficacy: no complete campaign DB'] : []),
    ...(gate?.conformance_pass == null ? ['conformance: efficacy was the active primary axis'] : []),
    ...(gate?.preservation_pass == null
      ? ['preservation: no existing assertion changed']
      : []),
    'brittleness: certified probe is not implemented',
    'redundancy: advisory analysis not run',
  ] : [
    'efficacy: no candidate edit reached the objective gate',
    'conformance: no candidate edit reached review',
    'preservation: no candidate edit to preserve',
    'brittleness: certified probe is not implemented',
    'redundancy: advisory analysis not run',
  ]
  const recorded = await agent(
    `Append one Sharpen ledger record and stop before outward action. ${REL} The ledger is \`.ledger.sharpen.jsonl\`. Touch ONLY the ledger.\n` +
    `Module ${pick.module}, tests ${JSON.stringify(pick.tests)}. Build the record with state "${state}", audited from \`date -u +%Y-%m-%dT%H:%M:%SZ\`, review ${reviewBlock}, and axes ${JSON.stringify(axes)}.\n` +
    `Set axes_not_applied exactly to ${JSON.stringify(axesNotApplied)}. Together the axes object and this list account for efficacy, conformance, preservation, brittleness, and redundancy (SPEC Self-reflection).\n` +
    `MUST append through \`uv run python tools/sharpen_ledger.py --ledger .ledger.sharpen.jsonl append --record-json '<record-json>'\`; the CLI owns source_sha, toolset_version, and contract_version. Return those CLI values as recorded_source_sha, recorded_toolset_version, recorded_contract_version, plus recorded_module and recorded_axes_not_applied=true only when the persisted list matches.\n` +
    (extraNote ? `note: ${extraNote}\n` : '') +
    `Do NOT open a PR, push, or file any issue. Leave the ledger append as the only change.\n`,
    { phase: 'Record', schema: RECORD, label: 'record' },
  )
  const valid = recorded?.ledger_appended === true && recorded.state === state &&
    recorded.recorded_module === pick.module && /^[0-9a-f]{64}$/.test(recorded.recorded_source_sha) &&
    Number.isInteger(recorded.recorded_toolset_version) && recorded.recorded_toolset_version >= 1 &&
    recorded.recorded_contract_version === CONTRACT_VERSION && recorded.recorded_axes_not_applied === true
  if (!valid) return { ...recorded, state: 'dry-run', ledger_appended: false, pr_url: null }
  if (!wantPr) return recorded
  const outward = await agent(
    `The validated Sharpen ledger receipt for ${pick.module} is complete. Open a PR on a fresh branch off main (feat/sharpen-<module-stem>): body per SPEC "PR body" — the WHAT (diff) + WHY a human should care in project terms, the four-axis before/after, the disposition, and gate report ${JSON.stringify(gate.report)}. Return its URL. Do not merge or edit the artifact.`,
    { phase: 'Record', schema: OUTWARD, label: 'outward' },
  )
  return typeof outward?.pr_url === 'string' && outward.pr_url ? { ...recorded, ...outward } : recorded
}
