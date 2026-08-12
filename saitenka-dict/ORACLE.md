# Differential oracle

The repository-only `oracle/yomitan_oracle.mjs` resource loads Yomitan's real `DictionaryImporter`,
`DictionaryDatabase`, and `Translator` from an external checkout. It is optional test tooling and is
not included in saitenka-dict's published distributions. No GPL implementation or fixture is copied
into saitenka-dict.

The request on stdin names the checkout and dictionary once plus a `queries` array; stdout is a result
array in the same order. The runner imports the dictionary once for the entire batch. The reusable
Python `HeadlessYomitanOracle` owns this request contract. Tests compare a stable semantic
projection, because `yomitan-api` explicitly does not promise stability for its internal response.

Install Yomitan's development dependencies in the external checkout once (`npm ci`), then invoke the
runner with Node. The repository-only `oracle/upstream-lock.json` records the reviewed revisions of
Yomitan, yomitan-api, and anki_miner. Updating a revision is a deliberate oracle review, never an
automatic fixture re-bless.

Run `uv run poe yomitan-parity` for the Markdown report or `uv run poe yomitan-parity-json` for machine
output. The report exits nonzero on drift and reports the first semantic difference. The browser-backed
`yomitan-api` remains useful for live checks against a user's installed dictionaries; the headless
runner is the deterministic CI/dev oracle.

For a rendering-structure diagnosis against an installed Saitenka database, run
`uv run poe dictionary-structure-oracle 鳥 --reading とり --dictionary 'Jitendex.org [2024-07-31]' --artifacts /tmp/tori-oracle`.
It sends the glossary returned by `saitenka-dict` through
Yomitan's real `StructuredContentGenerator`, compares its semantic DOM blocks with Saitenka's blocks
immediately before layout, and exits nonzero at the first glued, missing, reordered, or mismarked block.
Use `--artifacts <dir>` to retain the generated HTML and both traces.
