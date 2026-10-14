# Session v0 detection evidence

The seed corpus was run against reversible source faults before any old test was retired. Each focused
run failed and the source file was restored byte for byte.

| Scenario | Reversible fault | Observed failure |
|---|---|---|
| `loss-suspends-command-until-replacement` | bypass `CommandRuntime.perform`'s disconnected guard | a help surface appeared before the `lost-command-finished` checkpoint |
| `cue-change-rejects-same-batch-command` | omit `CueIdentityRetired` application in `PlaybackProjection` | both copy commands wrote `old`; the expected `new` write never appeared |
| `cue-change-rejects-same-batch-command` | mark the overlay toggle as cue-dependent | the cue-independent `Saitenka hidden` notice disappeared during cue retirement |
| `close-retires-visible-surface` | make `Overlay.remove_lifecycle_now` acknowledge without sending `overlay-remove` | the transcript terminated without `surface-absent` |
| `subtitle-navigation-emits-seeks` | map next-subtitle to the previous-subtitle delta | the expected `sub-seek 1` disappeared and a second `sub-seek -1` was emitted |
| `help-opens-pages-and-closes` | route help-next to help-previous | the second visible help-page write disappeared |
| `help-opens-pages-and-closes` | bypass help-modal rejection | the forbidden overlay toggle hid the help surface and emitted `Saitenka hidden` |
| `help-opens-pages-and-closes` | omit execution of every accepted command handler | the visible help transcript disappeared entirely |

`tests/contracts/test_adapter_protocol.py` also feeds a deliberately wrong transcript to the comparator and
requires it to fail. Raw mutation output and the byte-restoring runner live under ignored
`vibe/portable-corpus/` and `vibe/test-crossfile/`.
