/*
 * Dump Yomitan's French transform descriptor into the JSON schema `engine.py` loads.
 *
 * Sibling of the Japanese `japanese_transforms.json` (dumped from `japanese-transforms.js`): this reads
 * `ext/js/language/fr/french-transforms.js` from a yomidevs/yomitan checkout and serialises its
 * `frenchTransforms` descriptor to `{conditions, transforms}` — the exact shape `engine._load` expects
 * (`{type, in, out, re, de}` per rule). The committed JSON is the reviewable, GPL-3.0 derivative artifact.
 *
 * French pins to a DIFFERENT Yomitan commit than the JP data: the two rule sets are independent, and the
 * JP dump's pin (3af775bda1df) predates a stable French test. Keep this pin in sync with the French
 * corpus generator, not with the JP one.
 *
 *   YOMITAN_COMMIT = c0c3702963c2   (github.com/yomidevs/yomitan, GPL-3.0)
 *
 * Regenerate:  node deinflect/tools/dump_french_transforms.mjs <yomitan-checkout> > \
 *                  deinflect/src/saitenka_deinflect/data/french_transforms.json
 */

import {pathToFileURL} from 'node:url';
import path from 'node:path';

const checkout = process.argv[2] || `${process.env.HOME}/workspace/yomitan`;
const src = path.join(checkout, 'ext/js/language/fr/french-transforms.js');
const {frenchTransforms} = await import(pathToFileURL(src).href);

/** Condition tree: French conditions are flat leaves (no subConditions). */
const conditions = {};
for (const [name, cond] of Object.entries(frenchTransforms.conditions)) {
    conditions[name] = {sub: cond.subConditions ?? []};
}

/** Rules: mirror the JP dump's `{type, in, out, re, de}` key order for a stable diff. */
const transforms = {};
for (const [name, transform] of Object.entries(frenchTransforms.transforms)) {
    transforms[name] = transform.rules.map((rule) => ({
        type: rule.type,
        in: rule.conditionsIn,
        out: rule.conditionsOut,
        re: rule.isInflected.source,
        // suffix rules carry `.deinflected`; wholeWord rules only a `.deinflect()` closure.
        de: rule.type === 'wholeWord' ? rule.deinflect('') : rule.deinflected,
    }));
}

process.stdout.write(JSON.stringify({conditions, transforms}, null, 1) + '\n');
