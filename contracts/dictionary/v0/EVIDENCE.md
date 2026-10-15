# Dictionary compatibility evidence

Dictionary v0 owns ordered lookup, exact-term versus reading fallback, multi-dictionary composition,
frequency tie-breaking, stacked readings and phrases, and mining-card projection from abstract Yomitan
rows. Archive import, media loading, SQL telemetry, and concurrent cache lifetime stay native boundaries.

Returning all reading matches after an exact term hit makes `dictionary-lookup-laws` fail by merging the
reading-only homophone into the visible definition.
