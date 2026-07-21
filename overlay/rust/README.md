# rust/ — reserved for the future `overlay._native` cosmic-text raster backend

This directory is intentionally empty (Stage 8 groundwork). When the overlay needs a native
rasteriser, it lands here as a PyO3 extension module named `overlay._native`, implementing the
`overlay.raster.protocol.RasterBackend` protocol with [cosmic-text](https://github.com/pop-os/cosmic-text)
for shaping/layout/raster:

- **Input**: the existing pure-data row/block model (`panel.panel_rows` output over `sc/`
  structured-content blocks) — no PIL types cross the seam.
- **Output**: `RasterResult` — premultiplied BGRA (the canonical interchange at `mpvio/osd.py`) plus
  the layout-produced `ScanBox`/`LinkBox` hit geometry (a raster swap must never change hit
  geometry; `tests/test_layering.py` pins this).

**Hard requirement — free-threading:** the overlay runs on CPython 3.14t with the GIL disabled
(`PYTHON_GIL=0`), which is what makes the parallel prefetch render (~3.8× on 4 cores) possible. The
PyO3 module MUST declare free-threaded support (`pyo3::prelude` `#[pymodule(gil_used = false)]` /
abi with `Py_mod_gil = Py_MOD_GIL_NOT_USED`) — an extension without that declaration silently
re-enables the GIL for the whole process and destroys the parallel render win. Do not ship a build
that has not been verified GIL-off at runtime (`sys._is_gil_enabled()` in the doctor check).
