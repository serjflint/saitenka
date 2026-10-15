# Session v0 detection evidence

The seed corpus was run against reversible source faults before any old test was retired. Each focused
run failed and the source file was restored byte for byte.

| Scenario | Reversible fault | Observed failure |
|---|---|---|
| `loss-suspends-command-until-replacement` | bypass `CommandRuntime.perform`'s disconnected guard | a help surface appeared before the `lost-command-finished` checkpoint |
| `cue-change-rejects-same-batch-command` | omit `CueIdentityRetired` application in `PlaybackProjection` | both copy commands wrote `old`; the expected `new` write never appeared |
| `cue-change-rejects-same-batch-command` | mark the overlay toggle as cue-dependent | the cue-independent `Saitenka hidden` notice disappeared during cue retirement |
| `cue-change-rejects-same-batch-command` | flatten tokenized cue lines before copying | the clipboard wrote `newline` instead of `new\nline` |
| `startup-observes-playback` | reject cue commands before a cue has ever been installed | the visible `no line to copy` response disappeared |
| `close-retires-visible-surface` | make `Overlay.remove_lifecycle_now` acknowledge without sending `overlay-remove` | the transcript terminated without `surface-absent` |
| `subtitle-navigation-emits-seeks` | map next-subtitle to the previous-subtitle delta | the expected `sub-seek 1` disappeared and a second `sub-seek -1` was emitted |
| `help-opens-pages-and-closes` | route help-next to help-previous | the second visible help-page write disappeared |
| `help-opens-pages-and-closes` | bypass help-modal rejection | the forbidden overlay toggle hid the help surface and emitted `Saitenka hidden` |
| `help-opens-pages-and-closes` | omit execution of every accepted command handler | the visible help transcript disappeared entirely |
| `input-registration-publishes-effective-bindings` | lower the global mpv input section from `force` to `default` | the normalized section priority changed and the corpus failed |
| `input-registration-publishes-effective-bindings` | emit `disable-section` instead of `enable-section` | the required global enablement observation disappeared |
| `mining-adds-one-note` | skip the bulk miner's external `add_note` commit | no note reached the Anki boundary within the declared bound |
| `language-toggle-selects-secondary` | force every toggle back to the Japanese track | the observed `sid` write stayed on 2 instead of selecting English SID 1 |

`tests/contracts/test_adapter_protocol.py` also feeds a deliberately wrong transcript to the comparator and
requires it to fail. `contracts/evidence-v0.json` is the tracked witness ledger: it records the exact base,
production substitution, command, scenario, and observed mismatch for every reversible fault above.
