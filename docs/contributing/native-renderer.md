# Future cosmic-text raster backend

This is a possible future backend for Saitenka's dictionary-panel rasterization. It is not the
native-visible subtitle geometry path: `libasslite` delegates authored ASS layout to libass and returns
offscreen image layers only for hit-box extraction.

When Saitenka needs a native rasterizer, it should land as a PyO3 extension implementing
`saitenka.raster.protocol.RasterBackend` with [cosmic-text](https://github.com/pop-os/cosmic-text)
for shaping, layout, and rasterization. The extension module name and source location are intentionally
deferred until measured Pillow limits justify adding a Rust build.

- **Input**: the existing pure-data row/block model (`panel.panel_rows` output over `sc/`
  structured-content blocks) — no PIL types cross the seam.
- **Output**: `RasterResult` — premultiplied BGRA (the canonical interchange at `mpvio/osd.py`) plus
  the layout-produced `ScanBox`/`LinkBox` hit geometry (a raster swap must never change hit
  geometry; `tests/test_raster_backend.py` pins this).

**Hard requirement — free-threading:** Saitenka runs on CPython 3.14t with the GIL disabled
(`PYTHON_GIL=0`), which is what makes the parallel prefetch render (~3.8× on 4 cores) possible. The
PyO3 module MUST declare free-threaded support (`pyo3::prelude` `#[pymodule(gil_used = false)]` /
abi with `Py_mod_gil = Py_MOD_GIL_NOT_USED`) — an extension without that declaration silently
re-enables the GIL for the whole process and destroys the parallel render win. Do not ship a build
that has not been verified GIL-off at runtime (`sys._is_gil_enabled()` in the doctor check).
