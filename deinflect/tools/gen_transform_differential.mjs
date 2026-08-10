/*
 * Differential vector generator: run Yomitan's OWN LanguageTransformer over a seed corpus and record
 * every reached candidate, so `engine.py` can be diffed against the authoritative implementation.
 *
 * Why a differential, not a steal (cf. gen_yomitan_cases.py): Japanese ships an upstream conformance
 * suite (japanese-transforms.test.js) we transcribe. French ships NONE — so we generate the vectors by
 * running the real `LanguageTransformer.transform(source)` (fed `frenchTransforms`) over French inputs.
 * Because engine.py + french_transforms.json derive from the SAME upstream source, agreement is expected
 * by construction — so a diff is a pure defect detector for (a) engine.py port bugs and (b) lossy-dump
 * bugs (dump_french_transforms.mjs approximates `wholeWord` rules as `rule.deinflect('')`). Same "vendor
 * the generated vectors, pinned commit" posture as the JP corpus and the fsrs reference vectors; this
 * .mjs is a DEV-ONLY regenerator, never in `poe all`.
 *
 * RULE-DATA layer only: the raw seed is transformed directly, WITHOUT the language's text-preprocessors
 * (decap / elision / apostrophe) — exactly what `deinflect()` sees. Preprocessor fidelity is a tokenizer
 * concern (overlay), pinned end-to-end by the pipeline oracle. Keeping the layers separate is the point.
 *
 * Pin YOMITAN_COMMIT to the French dump's commit (the corpus must match the shipped rule data, not HEAD).
 *
 *   git -C ~/workspace/yomitan checkout c0c3702963c2
 *   node deinflect/tools/gen_transform_differential.mjs fr ~/workspace/yomitan
 */

import {pathToFileURL} from 'node:url';
import {readFileSync, writeFileSync} from 'node:fs';
import path from 'node:path';

const YOMITAN_COMMIT = 'c0c3702963c2';

// language → (descriptor module under ext/js/language/, exported descriptor name). One row per language
// with steal-able-by-differential rules; add a row to cover another language (Workstream A is reusable).
const LANGS = {
    fr: {module: 'fr/french-transforms.js', descriptor: 'frenchTransforms', fixture: 'french'},
};

const lang = process.argv[2] || 'fr';
const checkout = process.argv[3] || `${process.env.HOME}/workspace/yomitan`;
const spec = LANGS[lang];
if (!spec) {
    process.stderr.write(`no differential spec for language '${lang}' (known: ${Object.keys(LANGS).join(', ')})\n`);
    process.exit(2);
}

const here = path.dirname(new URL(import.meta.url).pathname);
const L = path.join(checkout, 'ext/js/language');
const {LanguageTransformer} = await import(pathToFileURL(path.join(L, 'language-transformer.js')).href);
const descriptor = (await import(pathToFileURL(path.join(L, spec.module)).href))[spec.descriptor];

const lt = new LanguageTransformer();
lt.addDescriptor(descriptor);

/** Parse the seed file: `# cat: name` opens a category; blank / other `#` lines ignored. */
function readSeeds(file) {
    const seeds = [];
    let category = 'uncategorized';
    for (const raw of readFileSync(file, 'utf8').split('\n')) {
        const line = raw.trim();
        const cat = line.match(/^#\s*cat:\s*(\S+)/);
        if (cat) { category = cat[1]; continue; }
        if (!line || line.startsWith('#')) { continue; }
        seeds.push({category, source: line});
    }
    return seeds;
}

const seedFile = path.join(here, 'differential_seeds', `${lang}.txt`);
const cases = [];
const seen = new Set(); // (category, source, term, reasons) — a seed may repeat across categories (pins)
for (const {category, source} of readSeeds(seedFile)) {
    for (const {text, trace} of lt.transform(source)) {
        if (trace.length === 0) { continue; } // the identity no-op (source itself, unreduced)
        const reasons = trace.map((frame) => frame.transform); // newest-first (== engine.py `chain`)
        const key = `${category}\x1f${source}\x1f${text}\x1f${reasons.join('\x1e')}`;
        if (seen.has(key)) { continue; } // Yomitan emits true duplicates; engine.py dedups them
        seen.add(key);
        // `rule: null` — the vector asserts term + reason-chain reachability (the French bug class);
        // the POS-condition check is skipped (candidate `conditions` is a bitmask, not a source name).
        cases.push({category, valid: true, source, term: text, rule: null, reasons});
    }
}

// `$CORPUS_OUT` redirects the write (the drift guard regenerates into a temp file to diff, never clobber).
const OUT = process.env.CORPUS_OUT || path.resolve(here, '..', 'tests', 'fixtures', `${spec.fixture}_transforms_cases.json`);
const header = (
    `Differential vectors: Yomitan's LanguageTransformer (${spec.descriptor}, ` +
    `github.com/yomidevs/yomitan @ ${YOMITAN_COMMIT}, GPL-3.0) run over deinflect/tools/` +
    `differential_seeds/${lang}.txt. Each row: deinflect(source, language='${lang}') must reach ` +
    `\`term\` via the exact reason-chain \`reasons\`. engine.py derives from the same upstream, so a ` +
    `diff detects port / lossy-dump bugs. Regenerate with gen_transform_differential.mjs (see docstring).`
);
writeFileSync(
    OUT,
    JSON.stringify({_source: header, language: lang, yomitan_commit: YOMITAN_COMMIT, cases}, null, 1) + '\n',
    'utf8',
);
process.stderr.write(`wrote ${cases.length} vectors (${new Set(cases.map((c) => c.source)).size} seeds) to ${OUT}\n`);
