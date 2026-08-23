# libasslite

`libasslite` is a small PyO3 wrapper around the public
[libass](https://github.com/libass/libass) rendering API. It owns the native library, renderer, and
track handles and returns copied immutable image layers; no native pointer escapes into Python.

The wheel contains only the wrapper. It loads the parity-tested libass 0.17.1–0.17.5 range from the
host at runtime, so applications can use the same system library as mpv. Set `LIBASSLITE_LIBRARY` or
pass `library_path=` when automatic discovery cannot find it. The separately licensed
`libasslite-bundle` package supplies a self-contained 0.17.5 runtime when installed; an explicit path
still wins, and `LIBASSLITE_BUNDLE=0` returns to system discovery.

```python
from pathlib import Path

import libasslite

renderer = libasslite.AssRenderer(
    ass_bytes,
    fonts=[("subtitle-font.ttf", font_bytes)],
    library_path=Path("/opt/homebrew/lib/libass.dylib"),
    fonts_dir="/home/me/.config/mpv/fonts",
    extract_fonts=True,
    default_family="sans-serif",
    font_provider=libasslite.FontProvider.AUTODETECT,
    features=[(libasslite.Feature.WRAP_UNICODE, True)],
)
result = renderer.render(
    timestamp_ms=1_500,
    frame_size=(3024, 1898),
    storage_size=(1920, 1080),
    margins=(98, 99, 0, 0),  # top, bottom, left, right
    use_margins=False,
    max_bitmap_bytes=16 * 1024 * 1024,
    style=libasslite.RenderStyle(font_scale=1.0, line_position=0.0),
)
renderer.close()
```

Four font sources are available, matching what a player can offer libass: the system providers, a
`fonts_dir` scanned for extra faces, `fonts=` bytes (`ass_add_font`, how a container's attachments
arrive), and an `[Fonts]` section inside the document when `extract_fonts=True`. Set
`font_provider=libasslite.FontProvider.NONE` to confine lookup to the first three.

`RenderStyle` carries the renderer state libass keeps between frames — font scale, line spacing and
position, hinting, shaper, and selective style override. It is passed per render because every member
of it is a function of the display geometry; omitting it restores libass's defaults, so one render's
result never depends on the render before it. `unsupported_features()` reports the track features
this libass build could not apply.

`AssRenderResult.layers` preserves libass list order. Each layer contains a tightly packed `bytes`
bitmap (`width * height`), placement, `0xRRGGBBAA` color, and public image type. Rendering releases
Python and is serialized per renderer, making one renderer safe to use from background workers.
`max_bitmap_bytes` stops traversal before copying a layer that would exceed the aggregate bitmap
budget; omit it only when the caller owns an equivalent bound.

## Installing libass

- macOS: `brew install libass`
- Debian/Ubuntu: install the runtime package providing `libass.so.9` (currently `libass9`)
- Windows: `vcpkg install libass:x64-windows`, then point `LIBASSLITE_LIBRARY` at the produced DLL

An existing mpv installation may already contain a usable library. Pass its exact DLL path rather than
copying it elsewhere; adjacent runtime dependencies must remain discoverable by the operating system.

## Scope

The package deliberately exposes only in-memory ASS loading, the four font sources, font lookup
defaults, track features, frame/storage geometry, frame margins and margin policy, the between-frame
renderer state, rendering, change detection, and copied image layers. It does not parse token spans,
control mpv, schedule work, cache geometry, or bundle fonts/libass.

## Licensing

`libasslite` is MIT. Its wheels do not distribute libass, FriBidi, HarfBuzz, FreeType, or a font
provider; the dynamically loaded system installation keeps its own license and deployment obligations.
See `NOTICE`.
