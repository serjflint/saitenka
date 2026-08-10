//! resvglite — a thin PyO3 binding of resvg for saitenka's inline dictionary glyphs.
//!
//! Yomitan structured content embeds inline `img` nodes that are almost always **SVG gaiji** (外字):
//! sense/section markers, orthography labels, reference glyphs (参照 / 表記 / 一). Pillow cannot
//! rasterize SVG, so the panel drew every one as a `▢` box. This crate exposes exactly one thing the
//! import path needs: SVG bytes → a PNG at a base resolution, which the DB stores and the renderer
//! composites (tinting monochrome glyphs to the text colour on the Python side).
//!
//! Pure Rust (usvg parser + tiny-skia raster), no system libraries — the same self-contained-wheel
//! bar as taffylite. Free-threading: `gil_used = false`; every call parses, renders, and drops its
//! tree with no shared state, so CPython does not re-enable the GIL on import. See issue #283.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use resvg::tiny_skia;
use resvg::usvg;

/// Rasterize `data` (SVG bytes) to a PNG `size_px` tall, preserving aspect. Returns `(png, w, h)`.
///
/// A base-resolution raster stored once at import; the renderer scales the PNG to the run's `sizeUnits`
/// via Pillow. `size_px` bounds the cold cost — gaiji are tiny, so 64px is crisp at any tooltip scale.
#[pyfunction]
fn render_svg(py: Python<'_>, data: &[u8], size_px: u32) -> PyResult<(Py<PyBytes>, u32, u32)> {
    if size_px == 0 {
        return Err(PyValueError::new_err("size_px must be positive"));
    }
    let opt = usvg::Options::default();
    let tree = usvg::Tree::from_data(data, &opt)
        .map_err(|e| PyValueError::new_err(format!("invalid SVG: {e}")))?;
    let isize = tree.size();
    if isize.height() <= 0.0 || isize.width() <= 0.0 {
        return Err(PyValueError::new_err("SVG has no intrinsic size"));
    }
    let scale = size_px as f32 / isize.height();
    let w = ((isize.width() * scale).round() as u32).max(1);
    let h = size_px;
    let mut pixmap =
        tiny_skia::Pixmap::new(w, h).ok_or_else(|| PyValueError::new_err("pixmap alloc failed"))?;
    resvg::render(&tree, tiny_skia::Transform::from_scale(scale, scale), &mut pixmap.as_mut());
    let png = pixmap
        .encode_png()
        .map_err(|e| PyValueError::new_err(format!("png encode: {e}")))?;
    Ok((PyBytes::new(py, &png).unbind(), w, h))
}

#[pymodule(gil_used = false)]
fn resvglite(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_svg, m)?)?;
    Ok(())
}
