# Config v0 detection evidence

Before the implementation-detail owners were retired, a reversible fault forced attach mode to ignore the
configured layout engine. `attach-overrides` failed with `default` instead of `taffy`; the source file was
then restored byte for byte. The same table compares run and attach normalization for tooltip, panel,
mining, key, annotation, and statistics settings.

Two additional reversible faults proved the editor rows before their point tests retired: forcing every
field to its built-in default lost both a top-level and nested on-disk value, while removing whitespace
normalization broke optional blank handling and preserved spaces around a string value.

The resolution rows killed two more faults: ignoring a mining preset selected `Lapis` instead of `Kiku`,
and rebuilding effective run configuration without the scoped base removed the palette, FSRS, and scoring
tables before dependency construction.

The exact substitutions, commands, scenarios, and mismatches are tracked in
`contracts/evidence-v0.json`.

The durable editor corpus also kills a shallow nested-table merge: after editing `mmap_size`, accepting the
next field's offered default must preserve the on-disk `cache_size_kib` sibling rather than its built-in value.
