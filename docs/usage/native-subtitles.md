# Native mpv subtitles with Saitenka interaction

The experimental native-visible mode lets mpv keep rendering the original ASS track while Saitenka
adds word scanning, dictionary tooltips, and mining. Use it when preserving the subtitle's typesetting
matters more than Saitenka's per-word subtitle colors.

```text
the same authored ASS cue
├─ mpv renders the original track (not Saitenka's rewritten copy)
└─ Saitenka derives invisible token hit boxes
   ├─ hover ──> focus outline
   └─ click/scan ──> the usual tooltip and mining features
```

Saitenka does not draw a second subtitle over mpv's after native pixel ownership is established.
Geometry readiness is independent: a cache miss or unsupported/failed geometry keeps the same mpv
subtitle visible, clears unproved hit boxes, and restores interaction asynchronously when a valid result
arrives. It does not flash the differently styled standard renderer between cues.

## Install the native runtime

The self-contained bundle is the simplest cross-platform choice:

```console
uv tool install "saitenka[full,subtitle-geometry-bundle]"
```

It ships for Linux x86_64/arm64, macOS arm64, and Windows x86_64. The bundle is a separate package
with its native components' notices and source metadata; the Saitenka core remains Apache-2.0.

To reuse an existing libass instead, install only the wrapper:

```console
uv tool install "saitenka[full,subtitle-geometry]"
```

`libasslite` searches in this order:

1. `[subtitle_geometry].library_path`;
2. `LIBASSLITE_LIBRARY`;
3. `libasslite-bundle`, when installed;
4. the operating system's normal libass locations.

Set `LIBASSLITE_BUNDLE=0` to bypass an installed bundle and test system discovery. An explicit path is
the escape hatch for package-manager, mpv-adjacent, or custom installations; keep the library beside
the native dependencies it was built with rather than copying one DLL or dylib elsewhere.

## Enable the mode

Add the toggle to `overlay.toml`:

```toml
[subtitle_geometry]
native_visible = true
```

The complete option reference, including the bounded result cache and cue lookahead, lives in
[`overlay.example.toml`](https://github.com/serjflint/saitenka/blob/main/overlay.example.toml). Run
`saitenka doctor` after enabling the mode; it reports whether the wrapper is installed and which
compatible libass can be initialized.

`saitenka run` configures the parity-tested mpv subtitle profile automatically. `saitenka attach`
cannot change how an existing player was launched, so that mpv instance needs these settings:

```conf
sub-ass-override=no
sub-ass-scale-with-window=no
sub-scale=1
sub-pos=100
sub-use-margins=yes
# Either value is supported; Saitenka mirrors the observed authored-ASS policy.
sub-ass-force-margins=no
sub-ass-video-aspect-override=0
sub-ass-use-video-data=all
sub-ass-style-overrides=
sub-font-provider=auto
embeddedfonts=no
sub-fonts-dir=
```

## Current supported envelope

The first mode supports UTF-8 external authored `.ass` files whose visible geometry can be reproduced
through libass's public API. It intentionally rejects unproved inputs instead of returning approximate
hit boxes.

The geometry runtime is selected independently of mpv's bundled or system libass. Saitenka cannot
introspect mpv's exact libass build and font environment, so a compatible runtime inside the supported
envelope can still differ at pixel level; this experimental mode does not claim runtime pixel identity.

Supported frames are static and use the mpv profile above, without attached/custom fonts or
application-level style overrides. A frame may contain several simultaneous ASS events: Saitenka
matches mpv's public `sub-text/ass-full` rows back to the authored source and keeps event identity in
each hit box. Whitespace and other non-painting tokenizer tokens remain available to the normal text
pipeline but are not required to produce libass pixels.

Saitenka mirrors mpv's frame, video storage size, pixel aspect, letterbox margins, and authored-ASS
margin policy, including Retina windows. Animated or unmatchable ASS, token-mapping failures, a
missing, oversized, or non-UTF-8 source, an unsupported font setup, a mismatched attach profile, or an
unavailable native runtime make that frame noninteractive while mpv keeps rendering it. Property changes
drained in one mpv poll are evaluated together, so intermediate `sub-start`/`sub-end`/ASS-row updates do
not cause a transient pixel-owner decision.

The standard renderer is catastrophic recovery, not geometry fallback. Saitenka uses it only after a
current visibility transaction tries to show mpv subtitles and reads back `sub-visibility=false` for a
nonempty selection. A rejected set without that current false readback, or a timed-out, stale, or
unreadable readback, leaves ownership unknown and uses a bounded retry instead of risking duplicate
subtitle pixels.

Geometry is prepared when the cue or render space changes and for a small lookahead window. Hovering,
scanning, and scrolling reuse those boxes, so interactive 60 FPS behavior does not require rendering
the subtitle geometry at 60 FPS.

## Troubleshooting

- Run `saitenka doctor` first. A missing wrapper/runtime is an installation problem; an unsupported
  render input means mpv is intentionally outside the tested envelope.
- If `run` works but `attach` does not, compare the attached player's options with the profile above.
- A native-geometry failure can temporarily remove scanning boxes, but the mpv subtitle style should
  remain stable. A switch to the standard renderer is a catastrophic native-visibility failure; include
  a report bundle if that occurs unexpectedly. Before reproducing, run `saitenka telemetry enable`;
  after the session, run `saitenka report`, then
  `saitenka subtitle-report /path/to/saitenka-report-*.zip`. The installed, text-free analyzer shows
  ownership transactions and retries, capability state, matched event/token counts, skip counts, and
  bounded error codes.
- Set `native_visible = false` to restore Saitenka's default redrawn, FSRS-colored subtitle.

For the provider contract, shadow-render pipeline, lifecycle guards, and package diagram, see
[Native-visible subtitle architecture](../contributing/architecture.md#native-visible-subtitle-architecture).
