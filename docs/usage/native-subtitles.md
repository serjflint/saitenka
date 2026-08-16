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

Saitenka does not draw a second subtitle over mpv's while native geometry is valid. If source,
render-profile, or provider validation fails, it hides mpv's subtitle layer and restores the standard
Saitenka renderer, preserving scanning, tooltips, and mining instead of approximating hit boxes.

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

Supported cues are static and use the mpv profile above, with no video margins, attached/custom fonts,
or application-level style overrides. Animated or ambiguous ASS, token-mapping failures, a missing or
non-UTF-8 source, letterboxing, an unsupported font setup, a mismatched attach profile, or an unavailable
native runtime switch back to the standard Saitenka renderer. The log and report record the stable
fallback reason; render-profile failures also name the rejected values.

Geometry is prepared when the cue or render space changes and for a small lookahead window. Hovering,
scanning, and scrolling reuse those boxes, so interactive 60 FPS behavior does not require rendering
the subtitle geometry at 60 FPS.

## Troubleshooting

- Run `saitenka doctor` first. A missing wrapper/runtime is an installation problem; an unsupported
  render input means mpv is intentionally outside the tested envelope.
- If `run` works but `attach` does not, compare the attached player's options with the profile above.
- A native-geometry failure should retain hover through the standard renderer. If it does not, include
  a `saitenka report` bundle in the bug report; it contains the fallback reason and worker counters.
- Set `native_visible = false` to restore Saitenka's default redrawn, FSRS-colored subtitle.

For the provider contract, shadow-render pipeline, lifecycle guards, and package diagram, see
[Native-visible subtitle architecture](../contributing/architecture.md#native-visible-subtitle-architecture).
