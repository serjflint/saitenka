import assert from 'node:assert/strict'
import fs from 'node:fs'

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const source = fs.readFileSync(new URL('harness.js', import.meta.url), 'utf8')
  .replace('export const meta =', 'const meta =')
const runHarness = new AsyncFunction('args', 'phase', 'agent', 'log', source)

const pick = {
  found: true,
  module: 'app/example.py',
  tests: ['tests/test_example.py'],
  status: 'unseen',
  score: 1,
  conformance: 1,
  actionable: 1,
  pr_exclusion_checked: true,
  outer_reflection_due: false,
  reason: 'fixture',
}

const green = {
  green: true,
  quarantined: [],
  before: { survival: null, conformance: 1, actionable: 1, db: null, survivor_func: null },
}

const efficacyGreen = {
  ...green,
  before: { ...green.before, survival: 0.4, db: '.mutation-cache/example.sqlite', survivor_func: 'score' },
}

const proposal = {
  applied: true,
  test_file: 'tests/test_example.py',
  cut_module: 'saitenka.app.example',
  touched_func: '',
  diff: 'fixture diff',
  proposals: [{ target_test: 'test_example', axis: 'Conformance', change: 'assert output', rationale: 'fixture' }],
}

const efficacyProposal = {
  ...proposal,
  touched_func: 'score',
  proposals: [{ target_test: 'test_example', axis: 'Efficacy', change: 'pin boundary', rationale: 'fixture' }],
}

const gate = {
  pass: true,
  anticheat_clean: true,
  efficacy_pass: null,
  conformance_pass: true,
  preservation_pass: null,
  restoration_verified: true,
  report: 'clean',
}
const efficacyGate = {
  ...gate,
  efficacy_pass: true,
  conformance_pass: null,
}
const receipt = (state) => ({
  state,
  ledger_appended: true,
  recorded_module: pick.module,
  recorded_source_sha: 'a'.repeat(64),
  recorded_toolset_version: 1,
  recorded_contract_version: 6,
  recorded_axes_not_applied: true,
})

async function scenario(responses, args = {}) {
  const calls = []
  const phases = []
  const agent = async (prompt, options = {}) => {
    const label = options.label ?? 'unlabelled'
    calls.push({ label, prompt })
    assert.ok(label in responses, `unexpected agent call: ${label}`)
    return responses[label]
  }
  const result = await runHarness(args, (name) => phases.push(name), agent, () => {})
  return { calls, phases, result }
}

{
  const result = await scenario({
    triage: pick,
    baseline: green,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    judge: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    'ship-gate': { pass: true, report: 'poe all: green' },
    record: receipt('in-progress'),
    outward: { pr_url: 'https://example.invalid/pr/1' },
  }, { openPr: true })
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.indexOf('judge') < labels.indexOf('ship-gate'))
  assert.ok(labels.indexOf('ship-gate') < labels.indexOf('record'))
  assert.ok(labels.indexOf('record') < labels.indexOf('outward'))
  assert.match(result.calls.find(({ label }) => label === 'ship-gate').prompt, /uv run poe all/)
  assert.match(result.calls.find(({ label }) => label === 'record').prompt,
    /preservation: no existing assertion changed/)
  assert.match(result.calls.find(({ label }) => label === 'record').prompt,
    /brittleness: certified probe is not implemented/)
  assert.equal(result.result.pr, 'https://example.invalid/pr/1')
}

{
  const lyingGate = {
    pass: true,
    anticheat_clean: false,
    efficacy_pass: false,
    conformance_pass: false,
    preservation_pass: false,
    restoration_verified: false,
    report: 'all arms failed',
  }
  const result = await scenario({
    triage: pick,
    baseline: green,
    'author#1': proposal,
    'gate#1': lyingGate,
    'revert#1': undefined,
    record: receipt('left-undone'),
  }, { openPr: true, maxRetries: 1 })
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.includes('revert#1'))
  assert.ok(!labels.includes('skeptic') && !labels.includes('outward'))
  assert.equal(result.result.state, 'left-undone')
}

{
  const result = await scenario({
    triage: pick,
    baseline: efficacyGreen,
    'author#1': efficacyProposal,
    'gate#1': efficacyGate,
    skeptic: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    judge: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    'ship-gate': { pass: true, report: 'poe all: green' },
    record: receipt('in-progress'),
    outward: { pr_url: 'https://example.invalid/pr/efficacy' },
  }, { openPr: true })
  const gatePrompt = result.calls.find(({ label }) => label === 'gate#1').prompt
  const recordPrompt = result.calls.find(({ label }) => label === 'record').prompt
  assert.match(gatePrompt, /sharpen_gate\.py efficacy/)
  assert.match(recordPrompt, /conformance: efficacy was the active primary axis/)
  assert.equal(result.result.pr, 'https://example.invalid/pr/efficacy')
}

{
  const result = await scenario({
    triage: pick,
    baseline: green,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    judge: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    'ship-gate': { pass: false, report: 'docs-refs failed' },
    'revert-ship-gate': undefined,
    record: receipt('dry-run'),
  }, { openPr: true })
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.includes('revert-ship-gate'))
  assert.ok(!result.calls.at(-1).prompt.includes('open a PR on a fresh branch'))
  assert.match(result.calls.at(-1).prompt, /Post-review ship gate failed; no PR opened/)
  assert.equal(result.result.state, 'dry-run')
}

{
  const red = { green: false, quarantined: ['test_example'], before: green.before }
  const result = await scenario({ triage: pick, baseline: red, record: receipt('dry-run') })
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
    skeptic: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    judge: {
      verdict: 'REFUTED',
      grounds: ['the marker alone does not change ordinary selectors'],
      constructed_bug: 'SAITENKA_LIVE=1 admits the test to the default universe',
      better_fix: {
        summary: 'exclude live in every ordinary selector',
        scope: 'outside-sharpen',
        evidence: 'test, test-ft, cov, and affected each define their own marker expression',
      },
    },
    'revert-dropped': undefined,
    record: receipt('dry-run'),
  })
  const record = result.calls.at(-1).prompt
  assert.match(record, /"skeptic_verdict":"UPHELD"/)
  assert.match(record, /"judge_verdict":"REFUTED"/)
  assert.match(record, /"verdict":"REFUTED"/)
  assert.match(record, /exclude live in every ordinary selector/)
  assert.match(record, /separate authorization required; never applied by this run/)
  assert.equal(result.calls.filter(({ label }) => label.startsWith('author#')).length, 1)
}

{
  const result = await scenario({
    triage: pick,
    baseline: green,
    'author#1': proposal,
    'gate#1': gate,
    skeptic: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    judge: { verdict: 'UPHELD', grounds: [], constructed_bug: null, better_fix: null },
    'revert-dryrun': undefined,
    record: receipt('dry-run'),
  })
  const labels = result.calls.map(({ label }) => label)
  assert.ok(labels.indexOf('revert-dryrun') < labels.indexOf('record'))
  assert.match(result.calls.at(-1).prompt, /"diff":"fixture diff"/)
  assert.match(result.calls.at(-1).prompt, /"verdict":"UPHELD"/)
}

{
  const result = await scenario({ triage: { ...pick, outer_reflection_due: true } })
  assert.deepEqual(result.phases, ['Select', 'Reflect'])
  assert.equal(result.result.reason, 'outer-reflection-due')
  assert.ok(!result.calls.some(({ label }) => label === 'baseline' || label === 'record'))
}

console.log('sharpen harness smoke: ok')
