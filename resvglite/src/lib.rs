//! resvglite — a thin PyO3 binding of resvg for saitenka's inline dictionary glyphs.
//!
//! Yomitan structured content embeds inline `img` nodes that are **SVG gaiji** (外字): sense/section
//! markers, orthography labels, reference glyphs (参照 / 表記 / 一). Two shapes occur in the wild:
//!   * **path-outlined** glyphs (Adobe Illustrator exports, e.g. 三省堂's 造 logo) — pure vector, no
//!     fonts needed; and
//!   * **`<text>` glyphs** (e.g. 大辞林's 漢 / 呉 reading-type badges: a `<rect>` box with a
//!     `<text>漢</text>` inside) — these need a font in usvg's database, or resvg silently draws only
//!     the box and drops the glyph, leaving an empty ▢-lookalike. That silent drop was the #283 tofu
//!     bug: the raster *succeeded*, so every media/decode check passed, yet the picture was a blank box.
//!
//! So `render_svg` takes optional font bytes (the caller — saitenka — passes its bundled NotoSansJP,
//! which covers the badge kanji) and, as a standalone fallback, can load the host's system fonts. When
//! a glyph still has no font, usvg logs a warning that our stderr bridge surfaces loudly (issue #283).
//!
//! Pillow cannot rasterize SVG, so this crate turns SVG bytes → a PNG at a base resolution, which the DB
//! stores and the renderer composites (tinting monochrome glyphs to the text colour on the Python side).
//!
//! Pure Rust (usvg parser + tiny-skia raster), no system libraries and no bundled font — the same
//! self-contained-wheel bar as taffylite; the font is the caller's to provide. Free-threading:
//! `gil_used = false`; every call parses, renders, and drops its tree with no shared state (the one-time
//! logger install is the only global, and it is idempotent), so CPython does not re-enable the GIL.

use std::sync::Once;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use resvg::tiny_skia;
use resvg::usvg;

/// A stderr logger so usvg's font-resolution warnings ("No match for 'X' font-family", a dropped
/// `<text>` glyph) are LOUD instead of swallowed — the #283 lesson that a font-less text render fails
/// silently. Warnings/errors only (usvg's info/debug is per-node noise). Installed once, process-wide.
struct StderrLog;

impl log::Log for StderrLog {
    fn enabled(&self, m: &log::Metadata) -> bool {
        m.level() <= log::Level::Warn
    }
    fn log(&self, record: &log::Record) {
        if self.enabled(record.metadata()) {
            eprintln!("resvglite[{}] {}", record.level(), record.args());
        }
    }
    fn flush(&self) {}
}

static LOGGER_INIT: Once = Once::new();

fn install_logger() {
    LOGGER_INIT.call_once(|| {
        // Ignore the error if the host already installed a global logger — we just miss the bridge then.
        if log::set_boxed_logger(Box::new(StderrLog)).is_ok() {
            log::set_max_level(log::LevelFilter::Warn);
        }
    });
}

/// Rasterize `data` (SVG bytes) to a PNG `size_px` tall, preserving aspect. Returns `(png, w, h)`.
///
/// `fonts` is a list of font-file bytes (TTF/OTF) loaded into usvg's database so `<text>` glyphs render;
/// set `load_system_fonts` to additionally pull the host's fonts (standalone use). With neither, a
/// `<text>` SVG rasterizes to its non-text shapes only and usvg warns via the stderr bridge.
///
/// A base-resolution raster stored once at import; the renderer scales the PNG to the run's `sizeUnits`
/// via Pillow. `size_px` bounds the cold cost — gaiji are tiny, so 64px is crisp at any tooltip scale.
#[pyfunction]
#[pyo3(signature = (data, size_px, fonts=None, load_system_fonts=false))]
fn render_svg(
    py: Python<'_>,
    data: &[u8],
    size_px: u32,
    fonts: Option<Vec<Vec<u8>>>,
    load_system_fonts: bool,
) -> PyResult<(Py<PyBytes>, u32, u32)> {
    if size_px == 0 {
        return Err(PyValueError::new_err("size_px must be positive"));
    }
    install_logger();
    let mut opt = usvg::Options::default();
    // Point every generic family (`sans-serif`, `serif`, …) at the first provided face: dictionary
    // `<text>` gaiji request `font-family='sans-serif'`, and `load_font_data` alone does NOT wire a
    // loaded font to a generic name — that mismatch is why a bare load still rendered nothing.
    let default_family = {
        let db = opt.fontdb_mut();
        for font in fonts.into_iter().flatten() {
            db.load_font_data(font);
        }
        let fam = db
            .faces()
            .next()
            .and_then(|f| f.families.first().map(|(n, _)| n.clone()));
        if let Some(name) = &fam {
            db.set_serif_family(name.clone());
            db.set_sans_serif_family(name.clone());
            db.set_cursive_family(name.clone());
            db.set_fantasy_family(name.clone());
            db.set_monospace_family(name.clone());
        }
        // System fonts (standalone fallback) load AFTER, so `fam` reflects only caller-provided fonts and
        // usvg's own generic defaults still resolve the host families when no font was provided.
        if load_system_fonts {
            db.load_system_fonts();
        }
        fam
    };
    if let Some(name) = default_family {
        opt.font_family = name;
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    // A NotoSans (Latin) face is enough to prove `<text>` renders ink; the failure mode (empty fontdb →
    // dropped glyph) is font-independent. Ported in spirit from resvg's text reference tests, which
    // likewise assert a `<text>` node produces coverage when a matching font is present.
    const TEST_FONT: &[u8] =
        include_bytes!("../../overlay/src/overlay/assets/fonts/NotoSans.ttf");
    const TEXT_SVG: &[u8] = br#"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>
        <text x='10' y='90' font-family='sans-serif' font-size='100' fill='black'>A</text></svg>"#;
    const BOXED_TEXT_SVG: &[u8] = br#"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>
        <rect width='128' height='128' fill='none' stroke='black' stroke-width='8'/>
        <text text-anchor='middle' x='50%' y='50%' dy='.35em' font-family='sans-serif'
              font-size='100' fill='black'>A</text></svg>"#;

    fn opaque_px(data: &[u8], fonts: Option<Vec<Vec<u8>>>) -> usize {
        let mut opt = usvg::Options::default();
        let fam = {
            let db = opt.fontdb_mut();
            for font in fonts.into_iter().flatten() {
                db.load_font_data(font);
            }
            let fam = db.faces().next().and_then(|f| f.families.first().map(|(n, _)| n.clone()));
            if let Some(name) = &fam {
                db.set_sans_serif_family(name.clone());
            }
            fam
        };
        if let Some(name) = fam {
            opt.font_family = name;
        }
        let tree = usvg::Tree::from_data(data, &opt).expect("parse");
        let mut pixmap = tiny_skia::Pixmap::new(64, 64).unwrap();
        let scale = 64.0 / tree.size().height();
        resvg::render(&tree, tiny_skia::Transform::from_scale(scale, scale), &mut pixmap.as_mut());
        pixmap.pixels().iter().filter(|p| p.alpha() > 0).count()
    }

    #[test]
    fn text_renders_ink_with_a_font() {
        let ink = opaque_px(TEXT_SVG, Some(vec![TEST_FONT.to_vec()]));
        assert!(ink > 100, "a glyph with a loaded font must produce ink, got {ink}");
    }

    #[test]
    fn text_is_dropped_without_a_font() {
        // The #283 bug in one assertion: no font → the `<text>` glyph vanishes entirely.
        assert_eq!(opaque_px(TEXT_SVG, None), 0);
    }

    #[test]
    fn boxed_text_without_font_leaves_only_the_box() {
        // The exact tofu shape: a bordered box with a font-less glyph rasterizes to just the border —
        // more than zero ink (so decode/opacity checks pass) yet visually an empty ▢.
        let border_only = opaque_px(BOXED_TEXT_SVG, None);
        let with_glyph = opaque_px(BOXED_TEXT_SVG, Some(vec![TEST_FONT.to_vec()]));
        assert!(border_only > 0, "the box border must render");
        assert!(with_glyph > border_only, "the glyph must add ink over the bare box");
    }
}
