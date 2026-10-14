# CLI v0 detection evidence

Before the finite parser examples retired, two reversible production faults were checked through the
executable corpus:

| Scenario | Reversible fault | Detection |
|---|---|---|
| `legacy-run-defaults` | change the run command's default subtitle languages | the normalized `slang` result changed from `ja,jpn,jp` to `en` |
| `toml-feeds-cli-defaults` | detach the Cyclopts app from the configured TOML path | every configured default disappeared |

The registry-driven all-command help check and legacy wrapper subprocess remain separate: they cover an
open command set and a real compatibility process boundary rather than finite parser examples.
