# Building the experimental mpv layout pair

Saitenka consumes retained layout through mpv IPC and shares its existing subtitle presentation
pipeline. The pure decoder lives in `saitenka_subtitles.mpv_layout`; acquisition and validation live
in `saitenka.app.mpv_layout_source`. No new renderer or color device is involved.

## Source pair

The experimental development pair uses outline-free libass v3 without additional flags or API fields.
mpv uses its existing `event_index` to check the exact rendered event and reports the restricted
`static-logical-v2` geometry profile. This is experimental fork API support, not an upstream release.

Install the build dependencies required by each project's build documentation first. These commands
use a private prefix and leave the system mpv/libass installation intact. Run from the Saitenka root:

```sh
mkdir -p vibe/layout-build
git clone https://github.com/serjflint/libass.git vibe/layout-build/libass
git -C vibe/layout-build/libass checkout a4e65d3ebe182742cb96b49148610606f3149c2b
git clone https://github.com/serjflint/mpv.git vibe/layout-build/mpv
git -C vibe/layout-build/mpv checkout 0983100ebde02f5f0ea66d3a96d26002a1776f44
git -C vibe/layout-build/mpv apply "$PWD/docs/contributing/patches/mpv-restricted-profile.patch"

layout_prefix="$PWD/vibe/layout-build/prefix"
meson setup vibe/layout-build/libass/build vibe/layout-build/libass \
  --prefix="$layout_prefix" -Ddefault_library=shared -Dtest=enabled
meson compile -C vibe/layout-build/libass/build
meson test -C vibe/layout-build/libass/build --print-errorlogs
meson install -C vibe/layout-build/libass/build

PKG_CONFIG_PATH="$layout_prefix/lib/pkgconfig:$layout_prefix/lib64/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
  meson setup vibe/layout-build/mpv/build vibe/layout-build/mpv \
  -Dtests=true -Dlibmpv=false -Dmanpage-build=disabled \
  -Dc_link_args="-Wl,-rpath,$layout_prefix/lib -Wl,-rpath,$layout_prefix/lib64"
meson compile -C vibe/layout-build/mpv/build
meson test -C vibe/layout-build/mpv/build --print-errorlogs
```

The patch SHA256 is `fdf5ce6a43c23464bfed63b711ea18e7cabb1a1320befb6ee54d2effc4362e74`.
Verify linkage with `otool -L vibe/layout-build/mpv/build/mpv` on macOS or
`ldd vibe/layout-build/mpv/build/mpv` on Linux: libass must resolve to the private prefix.
The current desktop qualification was performed on macOS; Linux and Windows are not yet qualified.

Set the top-level `mpv_path` in your Saitenka config to the resulting executable and enable
`subtitle_geometry.native_visible`. See [source selection](../usage/native-subtitles.md#geometry-source)
for the `auto`, `shadow`, and scan-only `mpv` modes. Attach mode preserves the existing player's options;
the profile requires `--sub-ass-override=no`, `--sub-scale=1`, requested libass pixel aspect 1, and final-output
rendering (`--blend-subtitles=no`).

## Qualification

```sh
SAITENKA_LIVE=1 SAITENKA_LAYOUT_MPV="$PWD/vibe/layout-build/mpv/build/mpv" \
  uv run pytest -q tests/test_live_mpv_layout.py
```

The live harness launches mpv with `--focus-on=never`. It exercises production session acquisition,
real mouse lookup, ordinary shadow-paint preservation, and scan-only karaoke/alpha behavior.
Logical box tests and point-in-time validation do not prove physical display synchronization.
`uv run poe all` covers the bounded decoder, stale completion fences, option ownership, and the
existing shared pipeline. The producer's existing Meson tests supplement these consumer checks.

## Expanded profile contract

`static-logical-v2` accepts static positions and positive horizontal/vertical scales, including
mixed runs, with nonzero output margins. Rectangles already include margins. The consumer retains
compatibility with v1's zero-margin contract. Rotation, clipping, movement, transforms, drawings,
and unqualified resets remain explicit refusals.

`event-track-index` supplies each event's `track_index` in the rendered track. It is valid only in
that retained snapshot, and establishes the same traversal order as mpv's text properties. It is
not a persistent document identifier. Multi-event binding requires matching active ASS rows, each
event's text and timing, and matching aggregate text. Missing or ambiguous evidence refuses binding.

Paint qualification comes from the prepared shadow events and survives caching. Static primary
colors are supported; karaoke, inline alpha and translucent reset styles remain scan-only in auto.
The whole frame is paint-suppressed if any event is unqualified; partial-event painting is not yet
represented by the draw contract.

The v2 development build has deterministic decoder/parser and same-render placement evidence.
Desktop end-to-end, attachment-font and latency qualification are still required before deployment.
