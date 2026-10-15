# CLI v0 detection evidence

Before the finite parser examples retired, two reversible production faults were checked through the
executable corpus:

| Scenario | Reversible fault | Detection |
|---|---|---|
| `legacy-run-defaults` | change the run command's default subtitle languages | the normalized `slang` result changed from `ja,jpn,jp` to `en` |
| `toml-feeds-cli-defaults` | detach the Cyclopts app from the configured TOML path | every configured default disappeared |
| `resource-name-precedence` | ignore explicit resource-name flags | the configured dictionary incorrectly replaced the flag value |
| `jimaku-fetch-policy` | remove the explicit-fetch override | an explicit `--jimaku` request stopped fetching |
| `mpv-launch-subtitle-order` | move the video before runtime and subtitle arguments | the complete argv transcript changed order |
| `native-geometry-version-floor` | change the inclusive 0.40 floor to exclusive | mpv 0.40 became unsupported |
| `profile-cli-commands` | restore ambient reader defaults on the profile editor | a French profile inherited Japanese dictionaries and pitch data |
| `profile-cli-commands` | leave the selector after removing its active profile | `active_profile` pointed at a missing table |
| `profile-cli-commands` | default every language to `unidic` | a French profile selected the Japanese tokenizer |

The registry-driven all-command help check and legacy wrapper subprocess remain separate: they cover an
open command set and a real compatibility process boundary rather than finite parser examples.

The exact substitutions, commands, scenarios, and mismatches are tracked in
`contracts/evidence-v0.json`.
