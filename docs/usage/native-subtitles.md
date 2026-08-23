# Native mpv subtitles with Saitenka interaction

The experimental native-visible mode lets mpv keep rendering the original ASS track while Saitenka
adds word scanning, dictionary tooltips, and mining. Use it when preserving the subtitle's typesetting
matters: the per-word colors come too, painted over mpv's own glyphs.

```text
the same cue
├─ mpv renders the original track (not Saitenka's rewritten copy)
└─ Saitenka derives token geometry from it
   ├─ each token colored in its reading state: redrawn, or tinted
   ├─ its JLPT level, if it has one: an underline in the level's color
   ├─ hover ──> focus outline
   └─ click/scan ──> the usual tooltip and mining features
```

The color is an overprint: mpv's glyphs stay, and each token is drawn again on top in the same
face, at the same size and place, so the authored outline and shadow keep framing it.

Some faces mpv's OSD renderer can never load — a font that came from the container's attachments or
from a `[Fonts]` section inside the `.ass`, which reach only its subtitle renderer. Those tokens are
colored a second way instead: the geometry measurement already drew them with the *right* font set,
so its own anti-aliased pixels are tinted and uploaded as an image. No second font lookup happens
anywhere, which is what keeps the color on the same glyph shapes mpv drew. The choice is per token,
so a release whose dialogue is a system font and whose signs are attachment-only gets both in the
same frame. A token the measurement resolved neither a face nor pixels for is left **uncolored** —
it keeps its hit box, its tooltip and its mining, and simply carries no reading state. Nothing is
ever drawn at a guess: a substitute face would put the wrong glyph shapes over the right word, which
is the one failure you could not see.

The JLPT level underline is drawn separately and is additive — a word can be both due for review and
N3, exactly as under the standard renderer. It is a vector rule under the hit box, so unlike the
color it needs no font and never stands down. This is also why nothing else uses an underline here:
one mark, one meaning.

Which faces mpv's OSD renderer can reach is worked out from how mpv builds it. That reasoning is
about a mechanism, but the answer is about your machine — which fonts are installed, and what your
font provider substitutes — so it can be right in general and wrong here. Saitenka therefore asks
mpv to lay the color out and report where it landed, once per set of faces per window size, and
compares that with its own measurement. A disagreement demotes those families for the rest of the
session, and the color falls back to the tinted raster rather than sitting on substitute glyph
shapes.

The check costs mpv a full render on its core thread, so it is spent where a stall does not show:
once at each track load, and afterwards only while playback is paused — which the first hover
supplies, since opening a tooltip pauses by default. Note what this protects and what it does not:
the hit boxes come from mpv's *subtitle* renderer and are unaffected, so an undetected disagreement
misplaces the color, never the clicks.

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
# "authored-ass" (default) takes only .ass tracks; "all" also takes the SubRip ones mpv converts.
native_formats = "authored-ass"
```

Two different things happen when this mode does not take something, and they are worth telling apart
before you turn it on:

- **A track it does not take** — whatever `native_formats` leaves out, so a SubRip track under the
  default but not under `"all"` — is drawn by Saitenka's own renderer instead, and stays scannable,
  hoverable and minable exactly as before. You lose mpv's typesetting on that track, not the words.
  `Ctrl+Shift+L` (`keys.legacy_renderer_key`) asks for that renderer deliberately, for a whole
  episode.
- **A cue it cannot measure** — karaoke, animation, a vector drawing — keeps mpv's own rendering and
  loses only its interaction: no hover, no tooltip, no mining, on that cue. The renderer does not
  switch, so nothing flickers; the words are simply not scannable until the next cue.

`"all"` means the tracks mpv converts *from SubRip*, not any text track. A `mov_text` or `webvtt`
stream is converted to ASS by a different libavcodec decoder, writing its own header and styles, and
Saitenka's own extraction transcodes it to `.srt` — so the file on disk is not the document mpv is
drawing. Those tracks are refused by name (`subtitle-source-conversion-unreproduced`) and drawn with
the standard renderer, which keeps them scannable.

A SubRip track is not rendered from its file: libavcodec converts it to ASS and mpv renders that,
applying a whole branch of styling it applies to no authored track. Under `native_formats = "all"`
Saitenka reconstructs that conversion — libavcodec's header, mpv's own subtitle style, the
aspect-corrected script resolution, and the letterbox-dependent font scale — around the cue rows mpv
reports, so the events are mpv's own and only the header is reproduced.

Cue lookahead works there too, by predicting the events libavcodec will produce for the cues ahead
straight from the `.srt`. A wrong prediction cannot put a box in the wrong place: the geometry cache
is keyed on the event rows, so a predicted row that disagrees with the one mpv reports simply misses
and the cue is measured on arrival, exactly as an unpredicted one is. Cues whose SubRip markup the
converter will not reproduce faithfully — a stray `<`, an unknown tag, a named font color — are left
out of the lookahead rather than guessed at.

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
# Either `no` or `yes` is supported; `video` is not.
blend-subtitles=no
sub-filter-sdh=no
```

`--blend-subtitles=yes` is reproduced. mpv draws the subtitle into the video texture instead of the
OSD surface, so the cue is laid out on the video's on-screen rectangle with no letterbox margins;
Saitenka rebuilds that rectangle from `osd-dimensions` and offsets the resulting boxes back onto the
screen. A `--video-crop` or `--video-rotate` is refused *while blending*, because both break the
assumption the rectangle is derived from; outside blending neither is read.

`--blend-subtitles=video` stays refused: it lays the cue out on the video texture *after* the user's
shader hooks, whose size a `--glsl-shader` can change and no property reports.
`--sub-scale-with-window` and `--sub-scale-by-window` are *not* refused: they are read and
reproduced.

`--sub-filter-sdh` is the other refusal. It rewrites a cue's text before libass sees it, so mpv is
drawing something the subtitle file does not contain and there is nothing to match the hit boxes
back to. `--sub-filter-regex` and `--sub-filter-jsre` keep their geometry — they can only drop a
whole cue, never rewrite one, and a dropped cue simply has nothing to lay out. They do cost the
instant half of `Alt+←/→`: the cue index is built from the subtitle file, the filters run between the
file and the screen, so with one active Saitenka hands navigation to mpv's own `sub-seek` rather than
render a line mpv may never show.

The font settings are not in that list. Saitenka reads `embeddedfonts`, `sub-fonts-dir`,
`sub-font-provider` and `sub-font` when a track loads and loads the same faces mpv did — the
container's font attachments, an `[Fonts]` section inside the `.ass`, and the directory
`--sub-fonts-dir` names (or the config directory's `fonts` when it is empty). Change one of them
mid-episode and the frame is refused until the next track load rather than measured in the wrong
faces; reselect the track to pick the change up.

## Current supported envelope

The mode supports UTF-8 authored `.ass` files whose visible geometry can be reproduced through
libass's public API, and — under `native_formats = "all"` — the SubRip tracks mpv converts. It
intentionally rejects unproved inputs instead of returning approximate hit boxes.

The geometry runtime is selected independently of mpv's bundled or system libass. Saitenka cannot
introspect mpv's exact libass build and font environment, so a compatible runtime inside the supported
envelope can still differ at pixel level; this experimental mode does not claim runtime pixel identity.

What it does claim is checked against pixels rather than argued from mpv's source. `poe smoke-live`
has mpv render a cue and encode the frame, then compares the ink against the boxes Saitenka measured
for the same document — including one case placed by alignment and margins rather than by `\pos`, so
the whole placement chain is in the comparison. On the development machine every edge agrees to
within one pixel, which is the anti-aliasing threshold. The suite carries a negative control that
measures at a frame size mpv did not use, so a differential that stopped detecting anything fails
rather than passes.

Supported frames are static and use the mpv profile above, without application-level style
overrides. A cue typeset with animation (`\t`, `\move`, `\fad`), karaoke, a vector drawing, an ASS
effect, bidirectional text, or a blur (`\blur`/`\be`, which spreads a word's ink past its own box and
makes neighbouring hit boxes overlap) is reported as `typesetting-unsupported` — a property of the
track rather than a failure, so no retry will change it and the report says so. A frame may contain several simultaneous ASS events: Saitenka
matches mpv's public `sub-text/ass-full` rows back to the authored source and keeps event identity in
each hit box. Whitespace and other non-painting tokenizer tokens remain available to the normal text
pipeline but are not required to produce libass pixels.

Saitenka mirrors mpv's frame, video storage size, pixel aspect, letterbox margins, and authored-ASS
margin policy, including Retina windows. Animated or unmatchable ASS, token-mapping failures, a
missing, oversized, or non-UTF-8 source, an unsupported font setup, a mismatched attach profile, or an
unavailable native runtime make that frame noninteractive while mpv keeps rendering it. Property changes
drained in one mpv poll are evaluated together, so intermediate `sub-start`/`sub-end`/ASS-row updates do
not cause a transient pixel-owner decision.

A geometry *outcome* never selects the renderer, but a subtitle *source* does. When the selected track
can never produce geometry — a format this configuration does not take, oversized, or not UTF-8 —
Saitenka draws that track with the standard renderer instead, so it stays scannable rather than
leaving the episode with mpv's pixels and no hit boxes. The decision is made once per track
selection, from a property of the source that cannot change under it, so the standard renderer still
never flashes between cues. Selecting a track the native path does take returns to it.

Beyond that, the standard renderer is catastrophic recovery, not geometry fallback. Saitenka uses it
only after a current visibility transaction tries to show mpv subtitles and reads back
`sub-visibility=false` for a nonempty selection. A rejected set without that current false readback, or
a timed-out, stale, or unreadable readback, leaves ownership unknown and uses a bounded retry instead
of risking duplicate subtitle pixels.

Geometry is prepared when the cue or render space changes and for a small lookahead window. Hovering,
scanning, and scrolling reuse those boxes, so interactive 60 FPS behavior does not require rendering
the subtitle geometry at 60 FPS. Tooltip metadata, raster preparation, and mpv publication are also
session-owned background work; rapid pointer or wheel input replaces older pending intent instead of
blocking the player event loop.

## Troubleshooting

- Run `saitenka doctor` first — it answers the installation question only: whether the wrapper and
  the libass runtime are present and load.
- Whether *mpv* is outside the envelope is a different question, and only `saitenka subtitle-report`
  answers it. Look for `subtitle-render-input-unsupported`, which names the option that did not
  match, and `subtitle-source-conversion-unreproduced` for a track kind the mode does not take.
- If `run` works but `attach` does not, that reason code is the fast way in; comparing the attached
  player's options against the profile above by eye is the slow one.
- A native-geometry failure can temporarily remove scanning boxes, but the mpv subtitle style should
  remain stable. A switch to the standard renderer is a catastrophic native-visibility failure; include
  a report bundle if that occurs unexpectedly. Before reproducing, run `saitenka telemetry enable`;
  after the session, run `saitenka report`, then pass the exact printed path to
  `saitenka subtitle-report /path/to/saitenka-report-20260816-181525.zip`. The installed, text-free analyzer shows
  ownership transactions and retries, capability state, matched event/token counts, skip counts, and
  bounded error codes.
- For responsive subtitles but a delayed tooltip or scroll, run `saitenka trace-report` on that same
  report path. Its hover, tooltip, and scroll summaries distinguish target lookup, preparation, visible
  paint, supersession, cancellation, and failure without recording the hovered text.
- `Ctrl+Shift+L` hands the current episode to Saitenka's own renderer without restarting, and hands
  it back. That is the way to compare the two engines on the same cue, and the answer when a track's
  typesetting is one the native path refuses: colored, scannable text instead of untouchable pixels.
  A report tells a deliberate switch from a native-visibility failure.
- Set `native_visible = false` to restore Saitenka's default redrawn, FSRS-colored subtitle.

For the provider contract, shadow-render pipeline, lifecycle guards, and package diagram, see
[Native-visible subtitle architecture](../contributing/architecture.md#native-visible-subtitle-architecture).
