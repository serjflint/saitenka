import assert from 'node:assert/strict'
import fs from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(new URL('harness.js', import.meta.url), 'utf8')
  .replace('export const meta =', 'const meta =')
const runHarness = new AsyncFunction('args', 'phase', 'agent', 'log', source)

const pick = {
  found: true,
  module: 'app/example.py',
  tests: ['overlay/tests/test_example.py'],
  status: 'unseen',
  score: 1,
  conformance: 1,
  actionable: 1,
  pr_exclusion_checked: true,
  reason: 'fixture',
}

const green = {
  green: true,
  quarantined: [],
  before: { survival: null, conformance: 1, actionable: 1, db: null, survivor_func: null },
}

const proposal = {
  applied: true,
  test_file: 'overlay/tests/test_example.py',
  cut_module: 'overlay.app.example',
  touched_func: '',
  diff: 'fixture diff',
  proposals: [{ target_test: 'test_example', axis: 'Conformance', change: 'assert output', rationale: 'fixture' }],
}

const gate = { pass: true, anticheat_clean: true, efficacy_pass: true, report: 'clean' }

async function scenario(responses) {
  const calls = []
  const phases = []
  const agent = async (prompt, options = {}) => {
    const label = options.label ?? 'unlabelled'
    calls.push({ label, prompt })
    assert.ok(label in responses, `unexpected agent call: ${label}`)
    return responses[label]
  }
  const result = await runHarness({}, (name) => phases.push(name), agent, () => {})
  return { calls, phases, result }
}

{
  const red = { green: false, quarantined: ['test_example'], before: green.before }
  const result = await scenario({ triage: pick, baseline: red, record: { state: 'dry-run', ledger_appended: true } })
  assert.deepEqual(result.phases, ['Select', 'Measure'])
  assert.match(result.calls.at(-1).prompt, /Known-green baseline unavailable/)
  assert.equal(result.result.state, 'dry-run')
}

{
  const result = await scenario({
    triage: pick,
    baseline: green,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: { verdict: 'UPHELD', grounds: [] },
    judge: { verdict: 'REFUTED', grounds: ['fixture refutation'] },
    'revert-dropped': undefined,
    record: { state: 'dry-run', ledger_appended: true },
  })
  const record = result.calls.at(-1).prompt
  assert.match(record, /"skeptic_verdict":"UPHELD"/)
  assert.match(record, /"judge_verdict":"REFUTED"/)
  assert.match(record, /"verdict":"REFUTED"/)
}

{
  const result = await scenario({
    triage: pick,
    baseline: green,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: { verdict: 'UPHELD', grounds: [] },
    judge: { verdict: 'UPHELD', grounds: [] },
    'revert-dryrun': undefined,
    record: { state: 'dry-run', ledger_appended: true },
  })
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.indexOf('revert-dryrun') < labels.indexOf('record'))
  assert.match(result.calls.at(-1).prompt, /Captured diff: "fixture diff"/)
  assert.match(result.calls.at(-1).prompt, /"verdict":"UPHELD"/)
}

console.log('sharpen harness smoke: ok')
