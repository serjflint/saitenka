import assert from 'node:assert/strict'
import fs from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(new URL('harness.js', import.meta.url), 'utf8')
  .replace('export const meta =', 'const meta =')
const runHarness = new AsyncFunction('args', 'phase', 'agent', 'log', source)

const gap = {
  found: true,
  module: 'app/example.py',
  target_symbol: 'app/example.py::example',
  dimension: 'relative path',
  kind: 'scenario',
  source: 'invariant',
  tests: ['overlay/tests/test_example.py'],
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
  test_file: 'overlay/tests/test_example.py',
  cut_module: 'overlay.app.example',
  cut_file: 'src/overlay/app/example.py',
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
  pass: true,
  additive_only: true,
  liveness_pass: true,
  context_pass: true,
  growth_pass: true,
  concurrency_pass: null,
  restoration_verified: true,
  arms_run: ['additive', 'liveness', 'growth-adhoc'],
  report: 'clean',
}
const upheld = { verdict: 'UPHELD', grounds: [], redundant_with: null, better_fix: null }
const reflection = { introspection: 'fixture', findings: [], appended: true, escalations: [] }

async function scenario(responses) {
  const calls = []
  const phases = []
  const agent = async (prompt, options = {}) => {
    const label = options.label ?? 'unlabelled'
    calls.push({ label, prompt })
    assert.ok(label in responses, `unexpected agent call: ${label}`)
    return responses[label]
  }
  const result = await runHarness({ openPr: true }, (name) => phases.push(name), agent, () => {})
  return { calls, phases, result }
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
    record: { state: 'closed', outcome: 'coverage-only', ledger_appended: true, pr_url: 'https://example.invalid/pr/1', filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  const author = result.calls.find(({ label }) => label === 'author#1').prompt
  assert.match(author, /tier=default/)
  assert.match(author, /boundary_seam=real pathlib\.Path/)
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.indexOf('test-design') < labels.indexOf('author#1'))
  assert.ok(labels.indexOf('judge') < labels.indexOf('ship-gate'))
  assert.ok(labels.indexOf('ship-gate') < labels.indexOf('record'))
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
    'ship-gate': { pass: false, report: 'docs-refs failed' },
    revert: undefined,
    record: { state: 'dry-run', outcome: null, ledger_appended: true, pr_url: null, filed_issues: [], filing_blocker: null, note: '' },
    reflect: reflection,
  })
  assert.ok(result.calls.some(({ label }) => label === 'revert'))
  assert.match(result.calls.find(({ label }) => label === 'record').prompt, /Post-review ship gate failed; no PR opened/)
  assert.equal(result.result.state, 'dry-run')
}

console.log('grow harness smoke: ok')
