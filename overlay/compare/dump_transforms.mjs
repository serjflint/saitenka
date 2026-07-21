// Dump Yomitan's Japanese transform descriptor → the GPL-3.0 deinflect package's data file.
// This tool reads Yomitan source; its output feeds the GPL-3.0 saitenka-overlay-deinflect package.
// Regenerate when upgrading Yomitan (run from overlay/):
//   node compare/dump_transforms.mjs > ../deinflect/src/saitenka_deinflect/data/japanese_transforms.json
// Edit the import path to point at your Yomitan checkout:
import {japaneseTransforms} from '/path/to/yomitan/ext/js/language/ja/japanese-transforms.js';
const rule = (r) => {
  const o = {type: r.type, in: r.conditionsIn, out: r.conditionsOut, re: r.isInflected.source};
  o.de = ('deinflected' in r) ? r.deinflected : r.deinflect('');   // wholeWord keeps it in a closure
  return o;
};
const conditions = {};
for (const [k, v] of Object.entries(japaneseTransforms.conditions))
  conditions[k] = {sub: v.subConditions || []};
const transforms = {};
for (const [k, t] of Object.entries(japaneseTransforms.transforms))
  transforms[k] = t.rules.map(rule);
process.stdout.write(JSON.stringify({conditions, transforms}));