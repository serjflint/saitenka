# Dictionary compatibility evidence

Dictionary v0 owns ordered lookup, exact-term versus reading fallback, multi-dictionary composition,
frequency tie-breaking, stacked readings and phrases, and mining-card projection from abstract Yomitan
rows. It also owns profile-language routing for missing-entry messages and inflection chains, including the
Japanese rule that trusts the tokenizer lemma instead of deinflecting it again. Archive import, media
loading, SQL telemetry, and concurrent cache lifetime stay native boundaries.

Returning all reading matches after an exact term hit makes `dictionary-lookup-laws` fail by merging the
reading-only homophone into the visible definition.

Routing a French inflection chain through Japanese rules, deinflecting an already-tokenized Japanese lemma,
or ranking a bare word ahead of its longer phrase all make `dictionary-lookup-laws` fail.
