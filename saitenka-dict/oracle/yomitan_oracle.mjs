#!/usr/bin/env node

import process from 'node:process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

const request = JSON.parse(await readStdin());
const root = path.resolve(request.checkout);
const fromYomitan = createRequire(path.join(root, 'package.json'));
const {IDBKeyRange, indexedDB} = fromYomitan('fake-indexeddb');
const asUrl = (relative) => pathToFileURL(path.join(root, relative)).href;

const {createDictionaryArchiveData} = await import(asUrl('dev/dictionary-archive-util.js'));
const {DictionaryDatabase} = await import(asUrl('ext/js/dictionary/dictionary-database.js'));
const {DictionaryImporter} = await import(asUrl('ext/js/dictionary/dictionary-importer.js'));
const {Translator} = await import(asUrl('ext/js/language/translator.js'));
const {chrome, fetch} = await import(asUrl('test/mocks/common.js'));
const {DictionaryImporterMediaLoader} = await import(
    asUrl('test/mocks/dictionary-importer-media-loader.js')
);
const {createFindKanjiOptions, createFindTermsOptions} = await import(
    asUrl('test/utilities/translator.js')
);

globalThis.indexedDB = indexedDB;
globalThis.IDBKeyRange = IDBKeyRange;
globalThis.fetch = fetch;
globalThis.chrome = chrome;
globalThis.self = {constructor: {name: 'Window'}};
globalThis.Worker = function Worker() {
    return {addEventListener() {}, terminate() {}};
};

const archive = await createDictionaryArchiveData(
    path.resolve(request.dictionaryDirectory),
    request.dictionaryName,
);
const database = new DictionaryDatabase();
await database.prepare();
const importer = new DictionaryImporter(new DictionaryImporterMediaLoader());
const imported = await importer.importDictionary(database, archive, {
    prefixWildcardsSupported: true,
    yomitanVersion: '0.0.0.0',
});
if (imported.errors.length > 0 || imported.result === null) {
    throw new Error(`Yomitan import failed: ${JSON.stringify(imported.errors)}`);
}

const translator = new Translator(database);
translator.prepare();
const queries = request.queries ?? [request];
const results = [];
for (const query of queries) {
    const optionsPresets = query.optionsPresets ?? request.optionsPresets;
    const options = optionsPresets
        ? query.kind === 'kanji'
            ? createFindKanjiOptions(request.dictionaryName, optionsPresets, query.options)
            : createFindTermsOptions(request.dictionaryName, optionsPresets, query.options)
        : hydrateOptions(query.options);
    results.push(
        query.kind === 'kanji'
            ? await translator.findKanji(query.text, options)
            : await translator.findTerms(query.mode, query.text, options),
    );
}
process.stdout.write(`${JSON.stringify(request.queries ? results : results[0])}\n`);

async function readStdin() {
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    return input;
}

function hydrateOptions(options) {
    return {
        ...options,
        enabledDictionaryMap: new Map(options.enabledDictionaryMap),
    };
}
