# yomitanlite

An application-neutral Python surface for importing and querying Yomitan dictionaries.
It preserves structured semantic data and offers Yomitan's five term result modes without
depending on Saitenka, mpv, Anki, fonts, or rendering.

Yomitan itself remains the differential oracle. Its GPL implementation and fixtures are not
vendored into this Apache-2.0 package.

The public seam is deliberately small:

- `LookupSource.lookup_terms(TermQuery) -> TermResult`
- `LookupSource.lookup_kanji(KanjiQuery) -> KanjiResult`
- `DictionaryAdmin` for import/list/remove lifecycle
- `HeadlessYomitanOracle.batch()` for reusable external-oracle queries

The semantic results retain headwords, source traces, definitions, tags, scores, sequences,
frequencies, pitch/IPA metadata, structured content, kanji readings/stats, and resolved media
dimensions. Deinflection rules remain in the separately licensed `saitenka-deinflect` package;
callers pass candidate inflections into `TermQuery`.

Storage uses the standard-library SQLite driver with fixed statements and bound parameters (including
`json_each(?)` for value lists). The explicit schema is small enough that an ORM or query builder would
add a dependency without improving query safety.
