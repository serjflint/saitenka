export const meta = {
  name: 'grow-loop',
  description: 'One-gap Grow run: triage+scenario-map → author (additive) → objective 4-arm gate → isolated skeptic → judge → ledger/PR',
  whenToUse: 'Idle-time test-GROWTH on the Saitenka overlay suite: write ONE missing test for an under-specified scenario/config/invariant/race. One gap per run; author≠skeptic isolation is structural. args.openPr=true reaches the human merge gate; default is a dry-run (ledger only).',
  phases: [
    { title: 'Select' },
    { title: 'Author' },
    { title: 'Objective gate' },
    { title: 'Skeptic' },
    { title: 'Judge' },
    { title: 'Record' },
    { title: 'Reflect' },
  ],
}

// Claude Workflow adapter for the provider-neutral contract in ADAPTERS.md. The deterministic judgment
// lives in the Python tools (grow_triage / grow_gate / grow_ledger); this script only orchestrates them
// and enforces the one thing a single context cannot self-provide: an adversarial review where the skeptic
// never sees the author's reasoning (.agents/grow/SPEC.md → Review architecture; ADAPTERS.md → Fidelity).
//
// The workflow runtime has no shell/fs, so every deterministic command runs INSIDE an executor agent that
// returns schema-validated structured output — the tool is deterministic, the agent is the runner.
//
// args: { module?: string, openPr?: boolean (default false → dry-run), maxRetries?: number (default 3) }

const cfg = args || {}
const CONTRACT_VERSION = 3 // mirrors contracts.json; the Workflow runtime cannot read local files
const OPEN_PR = cfg.openPr === true
const MAX_RETRIES = Number.isInteger(cfg.maxRetries) ? cfg.maxRetries : 3
const CWD = 'overlay' // poe tasks + tools run from overlay/, RELATIVE to the launch dir

// The run TRACE — a factual record of what the loop actually did this run, fed to the Reflect phase so it
// introspects on evidence, not vibes. Populated as the run proceeds; every terminal exit flows through
// finish() so a bounced / dropped / no-candidate run reflects too (those are the richest lessons).
const trace = { gap: null, retries: 0, gate: null, review: null, outcome: null, notes: [] }

// Worktree-safe: launch from a dedicated git worktree so executor edits can't touch the live tree. Every
// executor operates on paths RELATIVE to its inherited cwd — an absolute path would escape the worktree.
const REL = 'Run from the `' + CWD + '/` directory relative to your current working directory. Do NOT use ' +
  'absolute paths or `cd` outside the repo you were launched in — this run may be inside a git worktree.'

// Hard scope guard, plus the Grow-specific additive rule.
const GUARD = 'SCOPE: edit ONLY the one target test file named below, and ADDITIVELY (append asserts / a ' +
  'parametrize case / a PROFILES row / a new test — never alter or remove an existing assertion; that is ' +
  'Sharpen\'s job). Never edit any source/tool/config file, never install anything. If something blocks ' +
  'you, STOP and return the blocker verbatim — do not work around it by touching another file.'

// --- schemas (mirror contracts.json) ---------------------------------------------------------------

const GAP = {
  type: 'object', additionalProperties: false,
  required: ['found', 'module', 'target_symbol', 'dimension', 'kind', 'status', 'reason'],
  properties: {
    found: { type: 'boolean' },
    module: { type: 'string', description: 'module key relative to src/overlay, e.g. app/tooltip.py' },
    target_symbol: { type: 'string', description: 'module_key::dotted.symbol' },
    dimension: { type: 'string', description: 'the under-specified axis (context label / invariant / survivor / issue id)' },
    kind: { type: 'string', enum: ['scenario', 'concurrency'], description: 'scenario ⇒ arms 1-3; concurrency ⇒ arm 4' },
    source: { type: 'string', enum: ['survivor', 'dead_config', 'invariant', 'filed'] },
    tests: { type: 'array', items: { type: 'string' }, description: 'existing mapped test files to extend' },
    status: { type: 'string', description: 'grow_ledger status of the gap' },
    score: { type: 'number' },
    pr_exclusion_checked: { type: 'boolean', description: 'true ONLY if triage ran the open-PR exclusion (gh authed, no --no-network)' },
    reason: { type: 'string' },
  },
}

const PROPOSAL = {
  type: 'object', additionalProperties: false,
  required: ['applied', 'test_file', 'cut_module', 'cut_file', 'target_func', 'test_name', 'diff', 'proposals'],
  properties: {
    applied: { type: 'boolean' },
    test_file: { type: 'string', description: 'edited test path, repo-relative' },
    cut_module: { type: 'string', description: 'dotted code-under-test module' },
    cut_file: { type: 'string', description: 'CUT path relative to repo (e.g. src/overlay/app/config.py) — for context/growth-adhoc' },
    target_func: { type: 'string', description: 'production symbol the grown test exercises (scopes arm-1)' },
    test_name: { type: 'string', description: 'the grown test function name (arm-2 liveness / --deselect node)' },
    red_on_pristine: { type: 'boolean', description: 'grown test FAILS on unmutated code → outcome-class-2 latent bug' },
    // arm-1 (scenario gaps): a one-line scenario-encoding mutation of the CUT the grown test must KILL and
    // the existing suite must SURVIVE. Non-optional for kind=scenario — it certifies growth over covered code.
    mutant_find: { type: ['string', 'null'], description: 'exact CUT snippet to mutate (must occur once)' },
    mutant_replace: { type: ['string', 'null'], description: 'the scenario-violating replacement' },
    // arm-4 (concurrency gaps): the two test node ids of the shipped pair.
    regression_node: { type: ['string', 'null'], description: 'concurrency regression test node id' },
    control_node: { type: ['string', 'null'], description: 'concurrency negative-control test node id' },
    control_test: { type: ['string', 'null'], description: 'control test function name (for its liveness check)' },
    diff: { type: 'string' },
    reason: { type: ['string', 'null'] },
    proposals: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['target_test', 'dimension', 'change', 'rationale'],
        properties: {
          target_test: { type: 'string' }, dimension: { type: 'string' },
          change: { type: 'string' }, rationale: { type: 'string' },
          claimed_growth: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  },
}

const GATE = {
  type: 'object', additionalProperties: false,
  required: ['pass', 'arms_run', 'report'],
  properties: {
    pass: { type: 'boolean', description: 'additive-only AND every applicable arm clean' },
    additive_only: { type: 'boolean' },
    liveness_pass: { type: ['boolean', 'null'] },
    context_pass: { type: ['boolean', 'null'] },
    growth_pass: { type: ['boolean', 'null'] },
    concurrency_pass: { type: ['boolean', 'null'] },
    arms_run: { type: 'array', items: { type: 'string' } },
    report: { type: 'string', description: 'the exact PASS/BOUNCE lines, verbatim' },
  },
}

const REVIEW = {
  type: 'object', additionalProperties: false,
  required: ['verdict', 'grounds', 'redundant_with', 'better_fix'],
  properties: {
    verdict: { type: 'string', enum: ['UPHELD', 'REFUTED'] },
    grounds: { type: 'array', items: { type: 'string' }, description: 'evidence citing scenarios/mutants/lines, never authority' },
    redundant_with: { type: ['string', 'null'], description: 'an existing test that already pins this scenario, or null' },
    better_fix: {
      type: ['object', 'null'], additionalProperties: false,
      required: ['summary', 'scope', 'evidence'],
      properties: {
        summary: { type: 'string' },
        scope: { type: 'string', enum: ['target-test', 'outside-grow'] },
        evidence: { type: 'string' },
      },
    },
  },
}

const RECORD = {
  type: 'object', additionalProperties: false,
  required: ['state', 'ledger_appended'],
  properties: {
    state: { type: 'string', enum: ['closed', 'open', 'unclosable', 'filed', 'dry-run'] },
    outcome: { type: ['string', 'null'], enum: ['coverage-only', 'bug', 'robustness', 'design', null] },
    ledger_appended: { type: 'boolean' },
    pr_url: { type: ['string', 'null'] },
    filed_issues: { type: 'array', items: { type: 'string' } },
    note: { type: 'string' },
  },
}

const REFLECT_CATEGORIES = [
  'gate-composition', 'arm-limitation', 'triage-signal', 'discovery', 'cli-ergonomics',
  'cost-latency', 'review-fidelity', 'false-bounce', 'false-pass', 'other',
]

const REFLECTION = {
  type: 'object', additionalProperties: false,
  required: ['introspection', 'findings', 'appended', 'escalations'],
  properties: {
    introspection: { type: 'string', description: 'plainly what the loop did this run, from the trace' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['category', 'subject', 'severity', 'evidence', 'proposal', 'self_referential'],
        properties: {
          category: { type: 'string', enum: REFLECT_CATEGORIES },
          subject: { type: 'string', description: 'the loop weakness, stable across runs (the recurrence key)' },
          severity: { type: 'string', enum: ['low', 'medium', 'high'] },
          evidence: { type: 'string', description: 'the trace signal(s) that show it — never a guess' },
          proposal: { type: 'string', description: 'smallest concrete change to a loop tool/spec/harness' },
          self_referential: { type: 'boolean', description: 'true if it touches the reflection machinery itself → extra human scrutiny' },
        },
      },
    },
    appended: { type: 'boolean', description: 'the findings were written to .reflection.grow.jsonl' },
    escalations: { type: 'array', items: { type: 'string' }, description: 'subjects at recurrence ≥ 2 (human triages)' },
  },
}

// --- run -------------------------------------------------------------------------------------------

phase('Select')
const gap = await agent(
  `Run the Grow triage and pick the highest-value ORPHAN scenario gap. ${REL}\n` +
  `From ${CWD}/ run: \`uv run python tools/grow_triage.py --top 1\`. Set pr_exclusion_checked=true ONLY if it ran with the open-PR exclusion active (gh authenticated, NO --no-network); if gh is unauth you may add --no-network but then set pr_exclusion_checked=false (the harness will refuse to open a PR).\n` +
  (cfg.module ? `The maintainer pinned module=${cfg.module}; use it ONLY if triage lists it as a live (non-excluded) candidate.\n` : '') +
  `The "→ pick:" line names the module; map it to its test files. Build a scenario map for that module — its intents, edge conditions, and invariant families (agreement, cache-equivalence, back-restores-state, config-matrix corners) — and subtract what the coverage baseline already exercises and what the grow ledger (\`.ledger.grow.jsonl\`, parent of ${CWD}/) records closed-current/unclosable (\`tools/grow_ledger.py\`). Return the single highest-value orphan gap: target_symbol (module_key::dotted.symbol), dimension, and kind (concurrency iff a data race, else scenario). Return found=false if there is no live module or no orphan gap.`,
  { phase: 'Select', schema: GAP, label: 'triage' },
)

if (!gap || !gap.found) {
  log(`No live gap to grow — ${gap ? gap.reason : 'triage failed'}`)
  trace.notes.push(`no live candidate: ${gap ? gap.reason : 'triage failed'}`)
  return await finish({ done: false, reason: gap ? gap.reason : 'triage failed', openPr: OPEN_PR })
}
log(`gap: ${gap.target_symbol} [${gap.kind}] "${gap.dimension}" (${gap.status}) — ${gap.reason}`)
trace.gap = { target_symbol: gap.target_symbol, kind: gap.kind, dimension: gap.dimension, module: gap.module, status: gap.status }

// Fail-closed: only open a PR if the open-PR exclusion actually ran (SPEC → never grow a module with an
// open feature branch). If triage couldn't check it, force a dry-run.
const canOpenPr = OPEN_PR && gap.pr_exclusion_checked !== false
if (OPEN_PR && !canOpenPr) {
  log(`open-PR exclusion unverified (gh unauth / --no-network) — forcing dry-run for ${gap.module}`)
}

phase('Author')
// Author gets the MINIMUM decisive context (target symbol, orphan scenario, invariant family, existing
// test file to extend) — never a whole-repo dump. Retries carry only the prior bounce.
let proposal = null
let gate = null
let carry = ''
let authorInvocation = null
const testHint = (gap.tests && gap.tests.length) ? gap.tests.join(', ') : '(find the module\'s mapped test file)'

for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
  proposal = await agent(
    `You are the Grow AUTHOR. Write ONE test that closes the gap {${gap.target_symbol} · "${gap.dimension}"} for module ${gap.module} (kind=${gap.kind}). ${REL} ${GUARD}\n` +
    `EXTEND the existing test(s) (${testHint}) before adding a new file — append a PROFILES/ENTRY_FACTORIES row, a parametrize case, or an @example; a new file only when there is no home. If the gap is a family, emit a Hypothesis property / deal contract, not a bare example.\n` +
    `Assert OBSERVABLE behaviour (return value / emitted IPC / written note / a metamorphic oracle) — never a private attr, mock call-count, or pixels. The test MUST be GREEN on pristine code; if it goes red you found a real defect: set red_on_pristine=true, describe it, STOP (do not massage it green).\n` +
    (gap.kind === 'concurrency'
      ? `This is a CONCURRENCY gap: ship a PAIR of PASSING tests as in tests/test_cache_race.py, driven by \`blanket\` (opt-in \`grow\` group) — a regression (guard present → no error) AND a self-certifying negative control that unguards a throwaway instance and ASSERTS the bug reproduces. Set regression_node, control_node, control_test.\n`
      : `This is a SCENARIO gap: the test must exercise a configuration/combination the existing suite never does. You MUST also supply a one-line scenario-encoding mutation of the CUT (mutant_find → mutant_replace, mutant_find occurring exactly once) that your test KILLS and the existing suite SURVIVES — this is arm-1, non-optional; without it the gate can only prove dead-config, not growth over covered code.\n`) +
    `Return the additive diff, test_name, target_func (the production symbol exercised), cut_module (dotted path) and cut_file (repo-relative path, e.g. src/overlay/${gap.module}). If nothing is worth growing, return applied=false with the reason — never fabricate a vacuous test.\n` +
    (carry ? `\nPRIOR ATTEMPT BOUNCED — do not repeat it. Gate report:\n${carry}\n` : ''),
    { phase: 'Author', schema: PROPOSAL, label: `author#${attempt}` },
  )
  if (!proposal || !proposal.applied) { carry = 'author did not apply an edit'; continue }
  authorInvocation = `author#${attempt}`

  // Outcome-class 2: a red-on-pristine test is a latent BUG, not a gate failure. Route to a filed issue.
  if (proposal.red_on_pristine) {
    log(`grown test is RED on pristine code → latent bug (outcome class 2): ${proposal.reason ?? 'see proposal'}`)
    await revert(proposal)
    trace.notes.push('red-on-pristine → filed a product bug (outcome class 2)')
    const rec = await recordOutcome('filed', null, null, 'bug',
      `Grown test for "${gap.dimension}" went red on pristine code — a real defect. Author note: ${proposal.reason ?? '(see diff)'}. File a product issue; do not land the assertion green (green-trunk).`)
    trace.outcome = rec?.state ?? 'filed'
    return await finish({ done: true, target: gap.target_symbol, state: rec?.state ?? 'filed', outcome: 'bug', openPr: OPEN_PR })
  }

  phase('Objective gate')
  // Deterministic: the real adds-only diff + the applicable grow_gate arms for this gap kind.
  const tf = proposal.test_file.replace('overlay/', '')
  const oldTests = (gap.tests || []).map((t) => t.replace('overlay/', '')).join(' ')
  const armsSpec = gap.kind === 'concurrency'
    ? `Arm 4 (concurrency), both PASS + control-oracle-live: first run arm-2 liveness on the CONTROL — ` +
      `\`uv run python tools/grow_gate.py liveness ${proposal.control_node ? proposal.control_node.split('::')[0] : tf} --test ${proposal.control_test} --repo .\` — then ` +
      `\`uv run --group grow python tools/grow_gate.py concurrency --regression ${proposal.regression_node} --control ${proposal.control_node} --control-file ${proposal.control_node ? proposal.control_node.split('::')[0] : tf} --control-test ${proposal.control_test} --repo .\` ` +
      `(regression PASSES, control PASSES, control oracle LIVE). Set concurrency_pass; liveness/context/growth = null.`
    : `Arm 2 (liveness): \`uv run python tools/grow_gate.py liveness ${tf} --test ${proposal.test_name} --repo .\` (>=1 live assert or a pytest.raises block, no trivial/dead). ` +
      `Arm 1 (growth-adhoc, the STRONG growth proof): \`uv run python tools/grow_gate.py growth-adhoc --cut ${proposal.cut_file} --find ${JSON.stringify(proposal.mutant_find)} --replace ${JSON.stringify(proposal.mutant_replace)} --old ${oldTests} --new ${oldTests} ${tf} --deselect ${tf}::${proposal.test_name} --repo .\` (old SURVIVES the mutant, grown test KILLS it). ` +
      `Arm 3 (context-delta, the ALTERNATIVE growth proof for a dead-config gap with no clean mutant): \`uv run python tools/grow_gate.py context --cut ${proposal.cut_file} --old ${oldTests} --new ${oldTests} ${tf} --deselect ${tf}::${proposal.test_name} --repo .\` (a newly-lit line). NOTE arm-3 is LINE-level: a covered-but-under-specified BRANCH of an already-covered line lights no new line and BOUNCES here — that's expected; arm-1 carries it. Set liveness_pass/context_pass/growth_pass; concurrency_pass=null.`
  gate = await agent(
    `Run the deterministic Grow objective gate on the author's edit. ${REL} Report tool output VERBATIM. (Read-only tools; edit nothing.)\n` +
    `1. ADDITIVE check (Grow↔Sharpen boundary): \`uv run python tools/grow_gate.py additive ${tf} --repo .\` — it must report ONLY added asserts. Any altered/removed assert ⇒ mutative ⇒ Sharpen scope ⇒ additive_only=false ⇒ BOUNCE. (Do NOT use sharpen_gate anticheat here — it misses same-tier value changes.)\n` +
    `2. ${armsSpec}\n` +
    `pass (scenario) = additive_only AND liveness_pass AND (growth_pass OR context_pass) — arm-1 and arm-3 are ALTERNATIVE proofs of genuine growth (a killed scenario-mutant OR a newly-lit line); requiring BOTH would reject a covered-but-under-specified branch gap arm-1 proves but line-level arm-3 misses. A scenario gap with NEITHER arm-1 nor arm-3 → BOUNCE (no growth proof). pass (concurrency) = additive_only AND concurrency_pass. arms_run lists the arms actually executed. Quote every BOUNCE line.`,
    { phase: 'Objective gate', schema: GATE, label: `gate#${attempt}`, effort: 'low' },
  )
  trace.retries = attempt
  if (gate && gate.pass) { trace.gate = gate; break }
  trace.gate = gate
  carry = gate ? gate.report : 'gate execution failed'
  log(`attempt ${attempt} bounced: ${carry.split('\n')[0]}`)
  await revert(proposal)
  proposal = null
}

if (!proposal || !gate || !gate.pass) {
  trace.notes.push(`objective gate never cleared in ${MAX_RETRIES} attempts; last bounce: ${carry}`)
  const rec = await recordOutcome('unclosable', null, null, null,
    `No grown test cleared the objective gate in ${MAX_RETRIES} attempts. Last bounce: ${carry}`)
  trace.outcome = rec?.state ?? 'unclosable'
  return await finish({ done: true, target: gap.target_symbol, state: rec?.state ?? 'unclosable', openPr: OPEN_PR })
}

phase('Skeptic')
// ISOLATED refutation — the skeptic sees only factual WHAT + DIFF, never the author's reasoning (SycEval:
// preemptive authority framing increases regressive agreement; forward only what+diff, reason from code).
const whatOnly = JSON.stringify(proposal.proposals.map((p) => ({ target_test: p.target_test, dimension: p.dimension, change: p.change })))
const skeptic = await agent(
  `You are an adversarial reviewer. A NEW test for ${gap.module} claims to close a real scenario gap the suite currently misses. Try to REFUTE it. Reason ONLY from the artifact below and the code — you are NOT given the author's reasoning.\n\n` +
  `WHAT: ${whatOnly}\n\nDIFF:\n${proposal.diff}\n\n` +
  `${REL} Read the target symbol \`${proposal.target_func || '(scenario edit)'}\` and the edited test. Refute if the test is REDUNDANT (an existing test already pins this scenario — name it in redundant_with), VACUOUS / a change-detector (a tautology, an implementation detail, or a value read from the code under test), OVER-PRODUCED (a near-duplicate where one parametrize/@given is clearer), or SHOULD-HAVE-EXTENDED (a new file where appending a PROFILES row would inherit the corner). If the gap is real but this is the wrong intervention, return the smallest evidence-backed better_fix and its scope; a better fix never rescues this candidate. Cite scenarios/mutants/lines — never authority. Default REFUTED on genuine doubt.`,
  { phase: 'Skeptic', schema: REVIEW, label: 'skeptic' },
)
const skepticInvocation = 'skeptic'

// Two independent UPHOLDs to ship. A skeptic REFUTED drops immediately (default-drop, no judge rescue).
let verdict = 'REFUTED'
let judgeNote = 'skeptic REFUTED — dropped'
let judge = null
let judgeInvocation = null
if (skeptic?.verdict === 'UPHELD') {
  phase('Judge')
  judge = await agent(
    `You are a SECOND, independent adversarial reviewer (the first reviewer is not shown to you). A NEW test for ${gap.module} claims to close a real scenario gap the suite currently misses. Try to REFUTE it, reasoning ONLY from the artifact and the code.\n\n` +
    `WHAT: ${whatOnly}\n\nDIFF:\n${proposal.diff}\n\n` +
    `${REL} Read the target symbol \`${proposal.target_func || '(scenario edit)'}\` and the edited test. Refute if REDUNDANT (name it in redundant_with), VACUOUS / a change-detector, OVER-PRODUCED, or SHOULD-HAVE-EXTENDED. If the gap is real but this is the wrong intervention, return the smallest better_fix + scope; it never rescues this candidate. Cite scenarios/mutants/lines, never authority. Default REFUTED on genuine doubt.`,
    { phase: 'Judge', schema: REVIEW, label: 'judge' },
  )
  judgeInvocation = 'judge'
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
trace.review = review

phase('Record')
if (verdict !== 'UPHELD') {
  const refuter = skeptic?.verdict === 'REFUTED' ? skeptic : judge
  await revert(proposal)
  trace.notes.push(`review dropped the change (${judgeNote})`)
  const rec = await recordOutcome('dry-run', null, review, null,
    `Review dropped the change (${judgeNote}). Grounds: ${JSON.stringify(refuter?.grounds ?? [])}. ` +
    `Redundant with: ${refuter?.redundant_with ?? 'n/a'}. Better fix (separate authorization; never applied here): ${JSON.stringify(refuter?.better_fix ?? null)}`)
  trace.outcome = 'dry-run'
  return await finish({ done: true, target: gap.target_symbol, state: 'dry-run', verdict, openPr: OPEN_PR })
}

if (!canOpenPr) {
  // Dry-run keeps only the ledger record — revert the edit before recording so the target_sha hashes the
  // pristine symbol (the ledger keys on the TARGET SYMBOL, unaffected by reverting the test, but keep the
  // tree clean for the maintainer's review).
  await revert(proposal)
}
const rec = await recordOutcome(canOpenPr ? 'closed' : 'dry-run', proposal, review, 'coverage-only', null)
trace.outcome = rec?.state ?? (canOpenPr ? 'closed' : 'dry-run')
return await finish({ done: true, target: gap.target_symbol, state: rec?.state ?? (canOpenPr ? 'closed' : 'dry-run'), pr: rec?.pr_url ?? null, openPr: OPEN_PR })

// --- helpers ---------------------------------------------------------------------------------------

// The Reflect phase: an ISOLATED agent introspects the run TRACE, reflects on what was wrong / inefficient
// about the LOOP ITSELF, and files improvement proposals to `.reflection.grow.jsonl` — ADVISORY only, it
// NEVER edits the loop's tools (self-modification is more dangerous than the loop's test edits, which
// already never auto-merge). It runs at EVERY terminal exit (a bounced / dropped / no-candidate run is the
// richest lesson). See SPEC → Self-reflection.
async function finish(result) {
  phase('Reflect')
  await agent(
    `You are the Grow loop's SELF-REFLECTION agent — an INDEPENDENT introspector of the LOOP, not of the ` +
    `grown test. You did not run the loop; reason ONLY from the factual run trace below. ${REL}\n\n` +
    `RUN TRACE:\n${JSON.stringify(trace, null, 2)}\n\n` +
    `Do THREE things:\n` +
    `1. INTROSPECT — state plainly what the loop did this run (which arms ran/bounced/were n-a, retries, ` +
    `review verdicts, outcome, any notes).\n` +
    `2. REFLECT — was anything about the LOOP wrong, inefficient, or suboptimal? A false-bounce (a gate arm ` +
    `rejected a legitimate grow), a false-pass, an arm that was n-a when it should apply, a weak/inverted ` +
    `triage signal, a slow stage, a CLI that couldn't express what was needed, a review-fidelity gap. Be ` +
    `adversarial toward the loop; cite trace signals as evidence. If the run was clean and revealed nothing, ` +
    `file NOTHING — do not manufacture findings (anti-Goodhart).\n` +
    `3. IMPROVE — for each real finding, the SMALLEST concrete change to a loop TOOL/SPEC/harness (never the ` +
    `product code). Mark self_referential=true if the proposal touches the reflection machinery itself ` +
    `(needs extra human scrutiny). category ∈ ${JSON.stringify(REFLECT_CATEGORIES)}; severity low|medium|high.\n\n` +
    `Then APPEND each finding to the reflection ledger and report escalations. From ${CWD}/:\n` +
    `- the ledger \`.reflection.grow.jsonl\` is at the repo root (parent of ${CWD}/); if absent, create it ` +
    `with a manifest line \`{"type":"manifest","loop_version":1}\` first.\n` +
    `- for each finding compute finding_id + read recurrence with \`tools/grow_reflect.py\` (import it), and ` +
    `append a record {finding_id, run_id:${JSON.stringify(trace.gap?.target_symbol ?? 'no-candidate')}, ` +
    `category, subject, severity, evidence, proposal, self_referential, loop_version:(manifest)}.\n` +
    `- ADVISORY ONLY: do NOT edit any tool/spec/harness/product file; the ledger is the only write.\n` +
    `- report any finding whose recurrence ≥ 2 at the current loop_version as an ESCALATION (the human ` +
    `triages / a bump to loop_version marks it addressed). Do NOT open issues or PRs.`,
    { phase: 'Reflect', schema: REFLECTION, label: 'reflect', effort: 'low' },
  )
  return result
}

async function revert(prop) {
  await agent(
    `${REL} Run \`git checkout -- ${prop.test_file.replace('overlay/', '')}\` to discard the edit. Confirm the tree is clean.`,
    { phase: 'Record', label: 'revert', effort: 'low' },
  )
}

async function recordOutcome(state, prop, reviewResult, outcome, extraNote) {
  const reviewBlock = reviewResult ? JSON.stringify(reviewResult) : 'null (terminal outcome, no review reached)'
  const wantPr = canOpenPr && state === 'closed' && prop
  return agent(
    `Append one Grow ledger record and ${wantPr ? 'open the PR' : 'stop at the ledger (dry-run: no PR, no outward action)'}. ${REL} The ledger \`.ledger.grow.jsonl\` is at the repo root (parent of ${CWD}/). Touch ONLY the ledger (and, if opening a PR, git/gh).\n` +
    `Gap: source=${gap.source ?? 'invariant'}, target_symbol=${gap.target_symbol}, dimension="${gap.dimension}". Compute gap_id + target_sha with tools/grow_ledger.py, stamp \`examined\` from \`date -u +%Y-%m-%dT%H:%M:%SZ\`, toolset_version from the manifest.\n` +
    `state: "${state}"${outcome ? `, outcome: "${outcome}"` : ''}. review block: ${reviewBlock}. contract_version: ${CONTRACT_VERSION}.\n` +
    (prop ? `test: ${JSON.stringify(prop.test_name)}. decisions: ${JSON.stringify(prop.proposals.map(p => p.change))}. Captured diff: ${JSON.stringify(prop.diff)}.\n` : '') +
    `axes_not_applied: list every gate arm that was n/a for this ${gap.kind} gap and WHY (the silent-no-run guard — SPEC/ADAPTERS).\n` +
    (extraNote ? `note: ${extraNote}\n` : '') +
    (wantPr
      ? `Then open a PR on a fresh branch off main (feat/grow-<symbol-stem>): body per SPEC "PR body" — the scenario now pinned, why a human should care, the gate evidence (arms run), the outcome class ${JSON.stringify(outcome)}, and gate report ${JSON.stringify(gate.report)}. Return its url. Do NOT merge.\n`
      : `Do NOT open a PR, push, or file any issue. Leave the ledger append as the only change; print the PR body that WOULD be opened so the maintainer can review before enabling openPr.\n`),
    { phase: 'Record', schema: RECORD, label: 'record' },
  )
}
