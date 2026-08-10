# resvglite

A thin [PyO3](https://pyo3.rs) binding of [resvg](https://github.com/linebender/resvg) — the optional,
self-contained SVG rasterizer behind [saitenka](https://github.com/serjflint/saitenka)'s inline
dictionary glyphs (issue #283).

Yomitan structured-content dictionaries embed inline `img` nodes that are almost always **SVG gaiji**
(外字) — sense/section markers, orthography labels, reference glyphs (参照 / 表記 / 一). Pillow cannot
rasterize SVG, so saitenka drew each as a `▢` box. resvglite rasterizes an SVG to a PNG at import; the
renderer composites and tints it.

## API

```python
import resvglite

png_bytes, width, height = resvglite.render_svg(svg_bytes, size_px=64)
```

`render_svg(data, size_px)` rasterizes `data` (SVG bytes) to a PNG `size_px` tall, aspect-preserved,
returning `(png, w, h)`. Raises `ValueError` on invalid or zero-size SVG.

## Why a vendored binding

resvg is **pure Rust** (usvg parser + tiny-skia raster) — no system libraries (unlike cairosvg/pyvips),
so it ships prebuilt wheels: three per platform (pyo3 abi3 + abi3t, PEP 803) — abi3 (GIL 3.13+), cp314t
(free-threaded 3.14), abi3t (FT *and* GIL 3.15+, `gil_used = false`), the same self-contained-wheel bar
as [taffylite](../taffylite). It is an **optional** add-on: without it, saitenka falls back to the
`▢`/label placeholder.

## Licensing

resvglite itself is `MIT OR Apache-2.0`. It **vendors resvg/usvg/tiny-skia, which are MPL-2.0** (a
file-level weak copyleft): the MPL covers only those vendored source files, not this wrapper or any
code that merely imports it. Because resvglite is a *separately published, optional* wheel, that
copyleft stays contained to this package — the core saitenka dependency graph remains permissive. See
`NOTICE`.
