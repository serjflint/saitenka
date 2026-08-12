# saitenka-dict

An independent, application-neutral Python engine with high behavioral compatibility with Yomitan's
dictionary format and lookup semantics. It preserves structured semantic data and offers Yomitan's
five term result modes without depending on Saitenka, mpv, Anki, fonts, or rendering.

This is an unofficial compatibility implementation. It is not affiliated with or endorsed by the
[Yomitan project](https://github.com/yomidevs/yomitan) or yomidevs.

Yomitan itself remains the differential oracle. Its GPL implementation and fixtures are not
vendored into this Apache-2.0 package.

The public seam is deliberately small:

- `LookupSource.lookup_terms(TermQuery) -> TermResult`
- `LookupSource.lookup_kanji(KanjiQuery) -> KanjiResult`
- `DictionaryAdmin` for import/list/remove lifecycle

The semantic results retain headwords, source traces, definitions, tags, scores, sequences,
frequencies, pitch/IPA metadata, structured content, kanji readings/stats, and resolved media
dimensions. Deinflection rules remain in the separately licensed `saitenka-deinflect` package;
callers pass candidate inflections into `TermQuery`.

The source checkout also contains an optional differential-test oracle under `oracle/`. Its Python
harness, Node runner, and upstream revision lock are repository-only development resources; none is
included in the published distributions. `poe yomitan-parity` exercises the harness.

Storage uses the standard-library SQLite driver with fixed statements and bound parameters (including
`json_each(?)` for value lists). The explicit schema is small enough that an ORM or query builder would
add a dependency without improving query safety.
