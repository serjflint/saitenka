# libasslite

`libasslite` is a small PyO3 wrapper around the public
[libass](https://github.com/libass/libass) rendering API. It owns the native library, renderer, and
track handles and returns copied immutable image layers; no native pointer escapes into Python.

The wheel contains only the wrapper. It loads libass 0.17.x from the host at runtime, so applications
can use the same system library as mpv. Set `LIBASSLITE_LIBRARY` or pass `library_path=` when automatic
discovery cannot find it.

```python
from pathlib import Path

import libasslite

renderer = libasslite.AssRenderer(
    ass_bytes,
    fonts=[("subtitle-font.ttf", font_bytes)],
    library_path=Path("/opt/homebrew/lib/libass.dylib"),
)
result = renderer.render(
    timestamp_ms=1_500,
    frame_size=(1920, 1080),
    storage_size=(1920, 1080),
)
renderer.close()
```

`AssRenderResult.layers` preserves libass list order. Each layer contains a tightly packed `bytes`
bitmap (`width * height`), placement, `0xRRGGBBAA` color, and public image type. Rendering releases
Python and is serialized per renderer, making one renderer safe to use from background workers.

## Installing libass

- macOS: `brew install libass`
- Debian/Ubuntu: install the runtime package providing `libass.so.9` (currently `libass9`)
- Windows: `vcpkg install libass:x64-windows`, then point `LIBASSLITE_LIBRARY` at the produced DLL

An existing mpv installation may already contain a usable library. Pass its exact DLL path rather than
copying it elsewhere; adjacent runtime dependencies must remain discoverable by the operating system.

## Scope

The package deliberately exposes only in-memory ASS loading, memory fonts, frame/storage geometry,
rendering, change detection, and copied image layers. It does not parse token spans, control mpv,
schedule work, cache geometry, or bundle fonts/libass.

## Licensing

`libasslite` is MIT. Its wheels do not distribute libass, FriBidi, HarfBuzz, FreeType, or a font
provider; the dynamically loaded system installation keeps its own license and deployment obligations.
See `NOTICE`.

