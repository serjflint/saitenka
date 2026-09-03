import assert from 'node:assert/strict'
import fs from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(new URL('harness.js', import.meta.url), 'utf8')
  .replace('export const meta =', 'const meta =')
const runHarness = new AsyncFunction('args', 'phase', 'agent', 'log', source)

const gap = {
  found: true,
  selection_outcome: 'gap',
  module: 'app/example.py',
  target_symbol: 'app/example.py::example',
  dimension: 'relative path',
  kind: 'scenario',
  source: 'invariant',
  tests: ['tests/test_example.py'],
  status: 'unseen',
  score: 1,
  pr_exclusion_checked: true,
  reason: 'fixture',
}
const design = {
  tier: 'default',
  boundary_seam: 'real pathlib.Path',
  extension_point: 'append an @example',
  oracle_family: 'absolute-relative agreement',
  rationale: 'pure path normalization',
}
const proposal = {
  applied: true,
  test_file: 'tests/test_example.py',
  cut_module: 'saitenka.app.example',
  cut_file: 'src/saitenka/app/example.py',
  target_func: 'example',
  test_name: 'test_example_relative',
  red_on_pristine: false,
  mutant_find: 'return path',
  mutant_replace: 'return path.absolute()',
  regression_node: null,
  control_node: null,
  control_test: null,
  diff: 'fixture diff',
  reason: null,
  proposals: [{ target_test: 'test_example_relative', dimension: 'relative path', change: 'add example', rationale: 'fixture' }],
}
const gate = {
  pass: false, // deliberately wrong: the harness owns the disposition
  additive_only: true,
  liveness_pass: true,
  context_pass: false,
  growth_pass: true,
  concurrency_pass: null,
  restoration_verified: true,
  arms_run: ['additive', 'liveness', 'growth-adhoc', 'context-delta'],
  report: 'context bounced; growth passed',
}
const upheld = { verdict: 'UPHELD', grounds: [], redundant_with: null, better_fix: null }
const reflection = { introspection: 'fixture', findings: [], appended: true, escalations: [] }
const receipt = {
  ledger_appended: true,
  recorded_source: gap.source,
  recorded_target_symbol: gap.target_symbol,
  recorded_dimension: gap.dimension,
  recorded_gap_id: '0123456789abcdef',
  recorded_target_sha: 'fedcba9876543210',
  recorded_toolset_version: 1,
  recorded_contract_version: 8,
}
const pristinePass = { status: 'pass', report: 'requested nodes passed' }
const additivePass = { pass: true, report: 'additive only' }

async function scenario(responses, args = { openPr: true }) {
  const calls = []
  const phases = []
  const agent = async (prompt, options = {}) => {
    const label = options.label ?? 'unlabelled'
    calls.push({ label, prompt })
    if (label.startsWith('additive#') && !(label in responses)) return additivePass
    if (label.startsWith('pristine#') && !(label in responses)) return pristinePass
    assert.ok(label in responses, `unexpected agent call: ${label}`)
    return responses[label]
  }
  const result = await runHarness(args, (name) => phases.push(name), agent, () => {})
  return { calls, phases, result }
}

{
  const noGap = {
    ...gap,
    found: false,
    selection_outcome: 'no-orphan',
    target_symbol: '',
    dimension: '',
    reason: 'scenario map is already covered',
  }
  const audit = {
    state: 'no-gap',
    ledger_appended: true,
    recorded_audit_module: noGap.module,
    recorded_audit_sha: 'a'.repeat(64),
    recorded_toolset_version: 3,
    recorded_contract_version: 8,
  }
  const result = await scenario({ triage: noGap, 'record-no-gap': audit, reflect: reflection }, { openPr: false })
  assert.equal(result.result.audit.recorded_audit_module, noGap.module)
  assert.match(result.calls.find(({ label }) => label === 'record-no-gap').prompt, /state:"no-gap"/)
}

{
  const noLive = {
    ...gap,
    found: false,
    selection_outcome: 'no-live',
    reason: 'every candidate is excluded',
  }
  const result = await scenario({ triage: noLive, reflect: reflection }, { openPr: false })
  assert.ok(!result.calls.some(({ label }) => label === 'record-no-gap'))
}

{
  const halfMutant = { ...proposal, mutant_replace: null }
  const contextOnly = { ...gate, pass: true, context_pass: true, growth_pass: null, arms_run: ['additive', 'liveness', 'context-delta'] }
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': halfMutant,
    'gate#1': contextOnly,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
}

{
  const unchangedMutant = { ...proposal, mutant_replace: proposal.mutant_find }
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': unchangedMutant,
    'gate#1': gate,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
}

{
  const mismatchedCut = { ...proposal, cut_file: 'src/saitenka/app/other.py' }
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': mismatchedCut,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
  assert.ok(!result.calls.some(({ label }) => label === 'pristine#1'))
}

{
  const traversalGap = { ...gap, module: '../escape.py', target_symbol: '../escape.py::example' }
  const traversalProposal = {
    ...proposal,
    cut_file: 'src/saitenka/../escape.py',
    cut_module: 'saitenka...escape',
  }
  const result = await scenario({
    triage: traversalGap,
    'test-design': design,
    'author#1': traversalProposal,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, recorded_target_symbol: traversalGap.target_symbol, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
  assert.ok(!result.calls.some(({ label }) => label === 'additive#1'))
}

{
  const noGrowthProof = {
    ...gate,
    pass: true, // deliberately wrong in the other direction
    context_pass: false,
    growth_pass: false,
  }
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': noGrowthProof,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.ok(result.calls.some(({ label }) => label === 'revert'))
  assert.equal(result.result.state, 'unclosable')
}

{
  const missingGrowthResult = {
    ...gate,
    pass: true,
    context_pass: true,
    growth_pass: null,
  }
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': missingGrowthResult,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
}

{
  const duplicateArm = {
    ...gate,
    pass: true,
    arms_run: ['additive', 'liveness', 'growth-adhoc', 'context-delta', 'context-delta'],
  }
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': duplicateArm,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
}

{
  const concurrencyGap = { ...gap, kind: 'concurrency' }
  const concurrencyProposal = {
    ...proposal,
    mutant_find: null,
    mutant_replace: null,
    regression_node: 'tests/test_example.py::test_regression',
    control_node: 'tests/test_example.py::test_control',
    control_test: 'test_control',
  }
  const missingControlLiveness = {
    pass: true,
    additive_only: true,
    liveness_pass: false,
    context_pass: null,
    growth_pass: null,
    concurrency_pass: true,
    restoration_verified: true,
    arms_run: ['additive', 'liveness', 'concurrency'],
    report: 'control liveness bounced',
  }
  const result = await scenario({
    triage: concurrencyGap,
    'test-design': design,
    'author#1': concurrencyProposal,
    'gate#1': missingControlLiveness,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.ok(result.calls.some(({ label }) => label === 'revert'))
  assert.equal(result.result.state, 'unclosable')
}

{
  const concurrencyGap = { ...gap, kind: 'concurrency' }
  const unboundProposal = {
    ...proposal,
    mutant_find: null,
    mutant_replace: null,
    regression_node: 'tests/test_other.py::test_example_relative',
    control_node: 'tests/test_other.py::test_control',
    control_test: 'test_control',
  }
  const passingGate = {
    pass: true,
    additive_only: true,
    liveness_pass: true,
    context_pass: null,
    growth_pass: null,
    concurrency_pass: true,
    restoration_verified: true,
    arms_run: ['additive', 'liveness', 'concurrency'],
    report: 'all executor arms passed',
  }
  const result = await scenario({
    triage: concurrencyGap,
    'test-design': design,
    'author#1': unboundProposal,
    'gate#1': passingGate,
    revert: undefined,
    record: { state: 'unclosable', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false, maxRetries: 1 })
  assert.equal(result.result.state, 'unclosable')
}

{
  const concurrencyGap = { ...gap, kind: 'concurrency' }
  const concurrencyProposal = {
    ...proposal,
    mutant_find: null,
    mutant_replace: null,
    regression_node: 'tests/test_example.py::test_example_relative',
    control_node: 'tests/test_example.py::test_control',
    control_test: 'test_control',
  }
  const passingGate = {
    pass: true,
    additive_only: true,
    liveness_pass: true,
    context_pass: null,
    growth_pass: null,
    concurrency_pass: true,
    restoration_verified: true,
    arms_run: ['additive', 'liveness', 'concurrency'],
    report: 'all concurrency arms passed',
  }
  const result = await scenario({
    triage: concurrencyGap,
    'test-design': design,
    'author#1': concurrencyProposal,
    'gate#1': passingGate,
    skeptic: upheld,
    judge: upheld,
    revert: undefined,
    record: { state: 'dry-run', outcome: 'coverage-only', ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  }, { openPr: false })
  assert.equal(result.result.state, 'dry-run')
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: upheld,
    judge: upheld,
    'ship-gate': { pass: true, report: 'poe all: green' },
    record: { state: 'open', outcome: 'coverage-only', ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    outward: { pr_url: 'https://example.invalid/pr/1', filed_issues: [] },
    finalize: { state: 'open', outcome: 'coverage-only', ...receipt, pr_url: 'https://example.invalid/pr/1', filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  const author = result.calls.find(({ label }) => label === 'author#1').prompt
  assert.match(author, /tier=default/)
  assert.match(author, /boundary_seam=real pathlib\.Path/)
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.indexOf('test-design') < labels.indexOf('author#1'))
  assert.ok(labels.indexOf('judge') < labels.indexOf('ship-gate'))
  assert.ok(labels.indexOf('ship-gate') < labels.indexOf('record'))
  assert.ok(labels.indexOf('record') < labels.indexOf('outward'))
  assert.ok(labels.indexOf('outward') < labels.indexOf('finalize'))
  assert.match(result.calls.find(({ label }) => label === 'record').prompt, /grow_ledger\.py --ledger \.ledger\.grow\.jsonl append/)
  assert.equal(result.result.pr, 'https://example.invalid/pr/1')
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: upheld,
    judge: upheld,
    'ship-gate': { pass: true, report: 'poe all: green' },
    record: { state: 'open', outcome: 'coverage-only', ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    outward: { pr_url: 'https://example.invalid/pr/2', filed_issues: [] },
    finalize: { state: 'open', outcome: 'coverage-only', ...receipt, recorded_gap_id: '1111111111111111', pr_url: 'https://example.invalid/pr/2', filed_issues: [], filing_blocker: null, note: '' },
    recover: { state: 'open', outcome: 'coverage-only', ...receipt, pr_url: 'https://example.invalid/pr/2', filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  assert.equal(result.result.state, 'open')
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: upheld,
    judge: upheld,
    'ship-gate': { pass: true, report: 'poe all: green' },
    record: { state: 'open', outcome: 'coverage-only', ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    outward: { pr_url: null, filed_issues: [] },
    reflect: reflection,
  })
  assert.equal(result.result.state, 'open')
  assert.ok(!result.calls.some(({ label }) => label === 'finalize'))
}

{
  const { pr_exclusion_checked: _omitted, ...uncheckedGap } = gap
  const result = await scenario({
    triage: uncheckedGap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: upheld,
    judge: upheld,
    revert: undefined,
    record: { state: 'dry-run', outcome: 'coverage-only', ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  assert.equal(result.result.state, 'dry-run')
  assert.ok(!result.calls.some(({ label }) => label === 'ship-gate' || label === 'outward'))
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: upheld,
    judge: upheld,
    'ship-gate': { pass: true, report: 'poe all: green' },
    record: { state: 'closed', outcome: 'coverage-only', ...receipt, recorded_target_symbol: 'app/other.py::other', pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  assert.equal(result.result.state, 'dry-run')
  assert.equal(result.result.pr, null)
  assert.ok(!result.calls.some(({ label }) => label === 'outward'))
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'pristine#1': { status: 'test-failure', report: 'assertion failed after collection' },
    'bug-skeptic': { verdict: 'UPHELD', grounds: ['observable mismatch'] },
    'bug-judge': { verdict: 'UPHELD', grounds: ['production violates invariant'] },
    revert: undefined,
    record: { state: 'dry-run', outcome: 'bug', ...receipt, pr_url: null, filed_issues: [], filing_blocker: 'dry-run', note: '' },
    reflect: reflection,
  }, { openPr: false })
  assert.equal(result.result.outcome, 'bug')
  assert.ok(!result.calls.some(({ label }) => label === 'gate#1'))
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'pristine#1': { status: 'test-failure', report: 'production raised ValueError' },
    'bug-skeptic': { verdict: 'UPHELD', grounds: ['valid observable oracle'] },
    'bug-judge': { verdict: 'UPHELD', grounds: ['unexpected CUT exception'] },
    revert: undefined,
    record: { state: 'open', outcome: 'bug', ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    outward: { pr_url: null, filed_issues: ['#123'] },
    finalize: { state: 'filed', outcome: 'bug', ...receipt, recorded_gap_id: '2222222222222222', pr_url: null, filed_issues: ['#123'], filing_blocker: null, note: '' },
    recover: { state: 'open', outcome: 'bug', ...receipt, pr_url: null, filed_issues: ['#123'], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  assert.equal(result.result.state, 'open')
  assert.ok(result.calls.some(({ label }) => label === 'recover'))
  const outwardPrompt = result.calls.find(({ label }) => label === 'outward').prompt
  assert.match(outwardPrompt, /production raised ValueError/)
  assert.match(outwardPrompt, /fixture diff/)
}

{
  const result = await scenario({
    triage: gap,
    'test-design': design,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: upheld,
    judge: upheld,
    'ship-gate': { pass: false, report: 'docs-refs failed' },
    revert: undefined,
    record: { state: 'dry-run', outcome: null, ...receipt, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  assert.ok(result.calls.some(({ label }) => label === 'revert'))
  assert.match(result.calls.find(({ label }) => label === 'record').prompt, /Post-review ship gate failed; no PR opened/)
  assert.equal(result.result.state, 'dry-run')
}

console.log('grow harness smoke: ok')
