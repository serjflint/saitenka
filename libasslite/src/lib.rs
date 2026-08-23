//! Runtime-loaded ownership wrapper for the public libass rendering ABI.

use std::ffi::{c_char, c_int, c_longlong, c_void, CString, OsString};
use std::path::PathBuf;
use std::ptr;
use std::sync::Mutex;

use libloading::Library;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

type AssLibrary = c_void;
type AssRendererHandle = c_void;
type AssTrack = c_void;

const MIN_LIBASS_VERSION: u32 = 0x01701000;
const MAX_LIBASS_VERSION: u32 = 0x01705000;
const LIBRARY_ENV: &str = "LIBASSLITE_LIBRARY";

/// Mirror of `ASS_Style` (`ass_types.h`), which `ass_set_selective_style_override` takes by
/// pointer. Field order is ABI, not convention: it is pinned by the version range
/// `validate_version` accepts, and `treat_fontname_as_pattern` is kept because libass reads the
/// fields after it by offset even though the field itself does nothing.
#[repr(C)]
struct NativeStyle {
    name: *const c_char,
    font_name: *const c_char,
    font_size: f64,
    primary_colour: u32,
    secondary_colour: u32,
    outline_colour: u32,
    back_colour: u32,
    bold: c_int,
    italic: c_int,
    underline: c_int,
    strike_out: c_int,
    scale_x: f64,
    scale_y: f64,
    spacing: f64,
    angle: f64,
    border_style: c_int,
    outline: f64,
    shadow: f64,
    alignment: c_int,
    margin_l: c_int,
    margin_r: c_int,
    margin_v: c_int,
    encoding: c_int,
    treat_fontname_as_pattern: c_int,
    blur: f64,
    justify: c_int,
}

#[repr(C)]
struct NativeImage {
    w: c_int,
    h: c_int,
    stride: c_int,
    bitmap: *const u8,
    color: u32,
    dst_x: c_int,
    dst_y: c_int,
    next: *const NativeImage,
    image_type: c_int,
}

type LibraryVersion = unsafe extern "C" fn() -> c_int;
type LibraryInit = unsafe extern "C" fn() -> *mut AssLibrary;
type LibraryDone = unsafe extern "C" fn(*mut AssLibrary);
type RendererInit = unsafe extern "C" fn(*mut AssLibrary) -> *mut AssRendererHandle;
type RendererDone = unsafe extern "C" fn(*mut AssRendererHandle);
type ReadMemory =
    unsafe extern "C" fn(*mut AssLibrary, *mut c_char, usize, *const c_char) -> *mut AssTrack;
type FreeTrack = unsafe extern "C" fn(*mut AssTrack);
type AddFont = unsafe extern "C" fn(*mut AssLibrary, *const c_char, *const c_char, c_int);
type SetFonts = unsafe extern "C" fn(
    *mut AssRendererHandle,
    *const c_char,
    *const c_char,
    c_int,
    *const c_char,
    c_int,
);
type SetFontsDir = unsafe extern "C" fn(*mut AssLibrary, *const c_char);
type SetExtractFonts = unsafe extern "C" fn(*mut AssLibrary, c_int);
type TrackSetFeature = unsafe extern "C" fn(*mut AssTrack, c_int, c_int) -> c_int;
type SetSize = unsafe extern "C" fn(*mut AssRendererHandle, c_int, c_int);
type SetDouble = unsafe extern "C" fn(*mut AssRendererHandle, f64);
type SetInt = unsafe extern "C" fn(*mut AssRendererHandle, c_int);
type SetStyleOverride = unsafe extern "C" fn(*mut AssRendererHandle, *const NativeStyle);
type SetMargins = unsafe extern "C" fn(*mut AssRendererHandle, c_int, c_int, c_int, c_int);
type SetUseMargins = unsafe extern "C" fn(*mut AssRendererHandle, c_int);
type SetPixelAspect = unsafe extern "C" fn(*mut AssRendererHandle, f64);
type RenderFrame = unsafe extern "C" fn(
    *mut AssRendererHandle,
    *mut AssTrack,
    c_longlong,
    *mut c_int,
) -> *const NativeImage;

struct Api {
    library_version: LibraryVersion,
    library_done: LibraryDone,
    renderer_done: RendererDone,
    free_track: FreeTrack,
    set_frame_size: SetSize,
    set_storage_size: SetSize,
    set_margins: SetMargins,
    set_use_margins: SetUseMargins,
    set_pixel_aspect: SetPixelAspect,
    set_font_scale: SetDouble,
    set_line_spacing: SetDouble,
    set_line_position: SetDouble,
    set_hinting: SetInt,
    set_shaper: SetInt,
    set_selective_style_override_enabled: SetInt,
    set_selective_style_override: SetStyleOverride,
    render_frame: RenderFrame,
}

struct NativeRenderer {
    api: Api,
    library_handle: *mut AssLibrary,
    renderer: *mut AssRendererHandle,
    track: *mut AssTrack,
    library_path: String,
    unsupported_features: Vec<i32>,
    _library: Library,
}

// Every access to the three libass handles is serialized by AssRenderer.native.
unsafe impl Send for NativeRenderer {}

impl Drop for NativeRenderer {
    fn drop(&mut self) {
        unsafe {
            (self.api.free_track)(self.track);
            (self.api.renderer_done)(self.renderer);
            (self.api.library_done)(self.library_handle);
        }
    }
}

#[pyclass(frozen, get_all, skip_from_py_object)]
struct AssImageLayer {
    width: i32,
    height: i32,
    stride: i32,
    bitmap: Py<PyBytes>,
    color: u32,
    dst_x: i32,
    dst_y: i32,
    image_type: i32,
}

#[pyclass(frozen, get_all)]
struct AssRenderResult {
    layers: Vec<Py<AssImageLayer>>,
    detect_change: i32,
}

/// The `ASS_Style` a caller hands to selective style override. Named after the ASS format's own
/// fields so a port of a host's style table reads as a transcription.
#[pyclass(frozen, get_all)]
struct AssStyle {
    name: String,
    font_name: String,
    font_size: f64,
    primary_colour: u32,
    secondary_colour: u32,
    outline_colour: u32,
    back_colour: u32,
    bold: i32,
    italic: i32,
    underline: i32,
    strike_out: i32,
    scale_x: f64,
    scale_y: f64,
    spacing: f64,
    angle: f64,
    border_style: i32,
    outline: f64,
    shadow: f64,
    alignment: i32,
    margin_l: i32,
    margin_r: i32,
    margin_v: i32,
    encoding: i32,
    blur: f64,
    justify: i32,
}

/// Renderer state libass keeps between frames, gathered so a caller sets it as one consistent set.
/// It travels with `render` rather than the constructor because every member of it is a function of
/// the display geometry, which changes under a live track.
#[pyclass(frozen, get_all)]
struct RenderStyle {
    font_scale: f64,
    line_spacing: f64,
    line_position: f64,
    hinting: i32,
    shaper: i32,
    override_bits: i32,
    override_style: Option<Py<AssStyle>>,
}

#[pyclass]
struct AssRenderer {
    native: Mutex<Option<NativeRenderer>>,
}

fn load_symbol<T: Copy>(library: &Library, name: &[u8]) -> Result<T, String> {
    unsafe {
        library
            .get::<T>(name)
            .map(|symbol| *symbol)
            .map_err(|error| error.to_string())
    }
}

fn validate_size(name: &str, size: (i32, i32)) -> PyResult<()> {
    if size.0 <= 0 || size.1 <= 0 {
        return Err(PyValueError::new_err(format!("{name} must be positive")));
    }
    Ok(())
}

fn validate_pixel_aspect(pixel_aspect: Option<f64>) -> PyResult<()> {
    if pixel_aspect.is_some_and(|value| !value.is_finite() || value <= 0.0) {
        return Err(PyValueError::new_err(
            "pixel_aspect must be finite and positive",
        ));
    }
    Ok(())
}

fn validate_margins(frame_size: (i32, i32), margins: (i32, i32, i32, i32)) -> PyResult<()> {
    let (top, bottom, left, right) = margins;
    if [top, bottom, left, right]
        .into_iter()
        .any(|value| value < 0)
    {
        return Err(PyValueError::new_err("margins must be non-negative"));
    }
    if i64::from(top) + i64::from(bottom) >= i64::from(frame_size.1)
        || i64::from(left) + i64::from(right) >= i64::from(frame_size.0)
    {
        return Err(PyValueError::new_err(
            "margins must leave a positive video rectangle",
        ));
    }
    Ok(())
}

fn validate_choice(name: &str, value: i32, highest: i32) -> PyResult<()> {
    if (0..=highest).contains(&value) {
        Ok(())
    } else {
        Err(PyValueError::new_err(format!(
            "{name} must be between 0 and {highest}"
        )))
    }
}

fn validate_scale(name: &str, value: f64) -> PyResult<()> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(PyValueError::new_err(format!(
            "{name} must be finite and positive"
        )))
    }
}

fn c_string(name: &str, value: &str) -> PyResult<CString> {
    CString::new(value).map_err(|_| PyValueError::new_err(format!("{name} contains a NUL byte")))
}

fn optional_c_string(name: &str, value: Option<String>) -> PyResult<Option<CString>> {
    value.map(|value| c_string(name, &value)).transpose()
}

fn as_ptr_or_null(value: &Option<CString>) -> *const c_char {
    value.as_ref().map_or(ptr::null(), |value| value.as_ptr())
}

fn validate_version(version: u32) -> Result<(), String> {
    if (MIN_LIBASS_VERSION..=MAX_LIBASS_VERSION).contains(&version) {
        Ok(())
    } else {
        Err(format!(
            "unsupported libass ABI 0x{version:08x}; expected 0.17.1 through 0.17.5"
        ))
    }
}

fn default_library_names() -> &'static [&'static str] {
    #[cfg(target_os = "windows")]
    {
        &["ass.dll", "libass.dll", "libass-9.dll"]
    }
    #[cfg(target_os = "macos")]
    {
        &[
            "libass.dylib",
            "/opt/homebrew/lib/libass.dylib",
            "/usr/local/lib/libass.dylib",
        ]
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        &["libass.so.9", "libass.so"]
    }
}

fn library_candidates(explicit: Option<PathBuf>) -> Result<Vec<OsString>, String> {
    if let Some(path) = explicit {
        return Ok(vec![path.into_os_string()]);
    }
    if let Some(path) = std::env::var_os(LIBRARY_ENV) {
        if path.is_empty() {
            return Err(format!("{LIBRARY_ENV} is empty"));
        }
        return Ok(vec![path]);
    }
    Ok(default_library_names().iter().map(OsString::from).collect())
}

fn load_library(explicit: Option<PathBuf>) -> Result<(Library, String), String> {
    let candidates = library_candidates(explicit)?;
    let mut failures = Vec::new();
    for candidate in candidates {
        match unsafe { Library::new(&candidate) } {
            Ok(library) => return Ok((library, candidate.to_string_lossy().into_owned())),
            Err(error) => failures.push(format!("{}: {error}", candidate.to_string_lossy())),
        }
    }
    Err(format!(
        "could not load libass; set {LIBRARY_ENV} or pass library_path ({})",
        failures.join("; ")
    ))
}

fn bitmap_len(source: &NativeImage) -> Result<usize, &'static str> {
    if source.w < 0 || source.h < 0 || source.stride < source.w {
        return Err("libass returned invalid image dimensions");
    }
    if !(0..=2).contains(&source.image_type) {
        return Err("libass returned an unknown image type");
    }
    let width = usize::try_from(source.w).map_err(|_| "libass image is too large")?;
    let height = usize::try_from(source.h).map_err(|_| "libass image is too large")?;
    width.checked_mul(height).ok_or("libass image is too large")
}

unsafe fn copy_bitmap(source: &NativeImage, packed_len: usize) -> Result<Vec<u8>, &'static str> {
    if packed_len == 0 {
        return Ok(Vec::new());
    }
    if source.bitmap.is_null() {
        return Err("libass returned a null bitmap");
    }
    let width = usize::try_from(source.w).map_err(|_| "libass image is too large")?;
    let height = usize::try_from(source.h).map_err(|_| "libass image is too large")?;
    let stride = usize::try_from(source.stride).map_err(|_| "libass image is too large")?;
    let mut bitmap = vec![0; packed_len];
    for row in 0..height {
        let offset = row.checked_mul(stride).ok_or("libass image is too large")?;
        let src = unsafe { source.bitmap.add(offset) };
        let dst = &mut bitmap[row * width..(row + 1) * width];
        unsafe { ptr::copy_nonoverlapping(src, dst.as_mut_ptr(), width) };
    }
    Ok(bitmap)
}

/// Everything libass wants configured before a track is parsed or a renderer is built. Gathered
/// because the calls are order-sensitive: the fonts directory and the extract flag have to precede
/// the track parse that consumes them, and `ass_set_fonts` has to follow it.
struct FontSetup {
    fonts_dir: Option<String>,
    extract_fonts: bool,
    default_font: Option<String>,
    default_family: Option<String>,
    font_provider: i32,
    fontconfig_config: Option<String>,
}

fn open_native(
    explicit_path: Option<PathBuf>,
    mut ass: Vec<u8>,
    fonts: Vec<(String, Vec<u8>)>,
    setup: FontSetup,
    features: Vec<(i32, bool)>,
) -> PyResult<NativeRenderer> {
    validate_choice("font_provider", setup.font_provider, 4)?;
    let fonts_dir = optional_c_string("fonts_dir", setup.fonts_dir)?;
    let default_font = optional_c_string("default_font", setup.default_font)?;
    let default_family = optional_c_string("default_family", setup.default_family)?;
    let fontconfig_config = optional_c_string("fontconfig_config", setup.fontconfig_config)?;
    let fonts = fonts
        .into_iter()
        .map(|(name, data)| {
            let name = CString::new(name)
                .map_err(|_| PyValueError::new_err("font name contains a NUL byte"))?;
            let len = i32::try_from(data.len())
                .map_err(|_| PyValueError::new_err("font is too large"))?;
            Ok((name, data, len))
        })
        .collect::<PyResult<Vec<_>>>()?;
    let (library, library_path) = load_library(explicit_path).map_err(PyRuntimeError::new_err)?;
    let library_version: LibraryVersion =
        load_symbol(&library, b"ass_library_version\0").map_err(PyRuntimeError::new_err)?;
    let library_init: LibraryInit =
        load_symbol(&library, b"ass_library_init\0").map_err(PyRuntimeError::new_err)?;
    let library_done: LibraryDone =
        load_symbol(&library, b"ass_library_done\0").map_err(PyRuntimeError::new_err)?;
    let renderer_init: RendererInit =
        load_symbol(&library, b"ass_renderer_init\0").map_err(PyRuntimeError::new_err)?;
    let renderer_done: RendererDone =
        load_symbol(&library, b"ass_renderer_done\0").map_err(PyRuntimeError::new_err)?;
    let read_memory: ReadMemory =
        load_symbol(&library, b"ass_read_memory\0").map_err(PyRuntimeError::new_err)?;
    let free_track: FreeTrack =
        load_symbol(&library, b"ass_free_track\0").map_err(PyRuntimeError::new_err)?;
    let add_font: AddFont =
        load_symbol(&library, b"ass_add_font\0").map_err(PyRuntimeError::new_err)?;
    let set_fonts: SetFonts =
        load_symbol(&library, b"ass_set_fonts\0").map_err(PyRuntimeError::new_err)?;
    let set_frame_size: SetSize =
        load_symbol(&library, b"ass_set_frame_size\0").map_err(PyRuntimeError::new_err)?;
    let set_storage_size: SetSize =
        load_symbol(&library, b"ass_set_storage_size\0").map_err(PyRuntimeError::new_err)?;
    let set_margins: SetMargins =
        load_symbol(&library, b"ass_set_margins\0").map_err(PyRuntimeError::new_err)?;
    let set_use_margins: SetUseMargins =
        load_symbol(&library, b"ass_set_use_margins\0").map_err(PyRuntimeError::new_err)?;
    let set_pixel_aspect: SetPixelAspect =
        load_symbol(&library, b"ass_set_pixel_aspect\0").map_err(PyRuntimeError::new_err)?;
    let render_frame: RenderFrame =
        load_symbol(&library, b"ass_render_frame\0").map_err(PyRuntimeError::new_err)?;
    let set_fonts_dir: SetFontsDir =
        load_symbol(&library, b"ass_set_fonts_dir\0").map_err(PyRuntimeError::new_err)?;
    let set_extract_fonts: SetExtractFonts =
        load_symbol(&library, b"ass_set_extract_fonts\0").map_err(PyRuntimeError::new_err)?;
    let track_set_feature: TrackSetFeature =
        load_symbol(&library, b"ass_track_set_feature\0").map_err(PyRuntimeError::new_err)?;
    let set_font_scale: SetDouble =
        load_symbol(&library, b"ass_set_font_scale\0").map_err(PyRuntimeError::new_err)?;
    let set_line_spacing: SetDouble =
        load_symbol(&library, b"ass_set_line_spacing\0").map_err(PyRuntimeError::new_err)?;
    let set_line_position: SetDouble =
        load_symbol(&library, b"ass_set_line_position\0").map_err(PyRuntimeError::new_err)?;
    let set_hinting: SetInt =
        load_symbol(&library, b"ass_set_hinting\0").map_err(PyRuntimeError::new_err)?;
    let set_shaper: SetInt =
        load_symbol(&library, b"ass_set_shaper\0").map_err(PyRuntimeError::new_err)?;
    let set_selective_style_override_enabled: SetInt = load_symbol(
        &library,
        b"ass_set_selective_style_override_enabled\0",
    )
    .map_err(PyRuntimeError::new_err)?;
    let set_selective_style_override: SetStyleOverride =
        load_symbol(&library, b"ass_set_selective_style_override\0")
            .map_err(PyRuntimeError::new_err)?;
    let version = unsafe { library_version() } as u32;
    validate_version(version).map_err(PyRuntimeError::new_err)?;

    let library_handle = unsafe { library_init() };
    if library_handle.is_null() {
        return Err(PyRuntimeError::new_err("ass_library_init failed"));
    }
    // The order below is mpv's (`assobjects_init`, `sd_ass.c`): library configuration, then
    // attachments, then the parse that extracts an in-file [Fonts] section, and only then the
    // renderer and its font lookup — which is built from whatever the library holds by that point.
    unsafe {
        if let Some(fonts_dir) = &fonts_dir {
            set_fonts_dir(library_handle, fonts_dir.as_ptr());
        }
        set_extract_fonts(library_handle, c_int::from(setup.extract_fonts));
    }
    for (name, data, len) in fonts {
        unsafe { add_font(library_handle, name.as_ptr(), data.as_ptr().cast(), len) };
    }
    let track = unsafe {
        read_memory(
            library_handle,
            ass.as_mut_ptr().cast(),
            ass.len(),
            ptr::null(),
        )
    };
    if track.is_null() {
        unsafe { library_done(library_handle) };
        return Err(PyValueError::new_err("libass rejected the ASS document"));
    }
    let mut unsupported_features = Vec::new();
    for (feature, enable) in features {
        let applied =
            unsafe { track_set_feature(track, feature, c_int::from(enable)) };
        if applied != 0 {
            unsupported_features.push(feature);
        }
    }
    let renderer = unsafe { renderer_init(library_handle) };
    if renderer.is_null() {
        unsafe {
            free_track(track);
            library_done(library_handle);
        }
        return Err(PyRuntimeError::new_err("ass_renderer_init failed"));
    }
    unsafe {
        set_fonts(
            renderer,
            as_ptr_or_null(&default_font),
            as_ptr_or_null(&default_family),
            setup.font_provider,
            as_ptr_or_null(&fontconfig_config),
            1,
        )
    };
    Ok(NativeRenderer {
        api: Api {
            library_version,
            library_done,
            renderer_done,
            free_track,
            set_frame_size,
            set_storage_size,
            set_margins,
            set_use_margins,
            set_pixel_aspect,
            set_font_scale,
            set_line_spacing,
            set_line_position,
            set_hinting,
            set_shaper,
            set_selective_style_override_enabled,
            set_selective_style_override,
            render_frame,
        },
        library_handle,
        renderer,
        track,
        library_path,
        unsupported_features,
        _library: library,
    })
}

/// Push the sticky renderer knobs for this frame. Always called, with libass's own defaults when
/// the caller passed no style, because the renderer keeps the previous frame's values otherwise —
/// which would make a render's result depend on the render before it.
fn apply_render_style(native: &NativeRenderer, style: Option<&RenderStyle>) -> PyResult<()> {
    let renderer = native.renderer;
    let (font_scale, line_spacing, line_position, hinting, shaper) = match style {
        Some(style) => (
            style.font_scale,
            style.line_spacing,
            style.line_position,
            style.hinting,
            style.shaper,
        ),
        None => (1.0, 0.0, 0.0, 0, 1),
    };
    unsafe {
        (native.api.set_font_scale)(renderer, font_scale);
        (native.api.set_line_spacing)(renderer, line_spacing);
        (native.api.set_line_position)(renderer, line_position);
        (native.api.set_hinting)(renderer, hinting);
        (native.api.set_shaper)(renderer, shaper);
    }
    let override_bits = style.map_or(0, |style| style.override_bits);
    unsafe { (native.api.set_selective_style_override_enabled)(renderer, override_bits) };
    let Some(source) = style.and_then(|style| style.override_style.as_ref()) else {
        return Ok(());
    };
    let source = source.get();
    // libass copies the strings, so these two live only as long as the call below.
    let name = c_string("name", &source.name)?;
    let font_name = c_string("font_name", &source.font_name)?;
    let native_style = NativeStyle {
        name: name.as_ptr(),
        font_name: font_name.as_ptr(),
        font_size: source.font_size,
        primary_colour: source.primary_colour,
        secondary_colour: source.secondary_colour,
        outline_colour: source.outline_colour,
        back_colour: source.back_colour,
        bold: source.bold,
        italic: source.italic,
        underline: source.underline,
        strike_out: source.strike_out,
        scale_x: source.scale_x,
        scale_y: source.scale_y,
        spacing: source.spacing,
        angle: source.angle,
        border_style: source.border_style,
        outline: source.outline,
        shadow: source.shadow,
        alignment: source.alignment,
        margin_l: source.margin_l,
        margin_r: source.margin_r,
        margin_v: source.margin_v,
        encoding: source.encoding,
        treat_fontname_as_pattern: 0,
        blur: source.blur,
        justify: source.justify,
    };
    unsafe { (native.api.set_selective_style_override)(renderer, &native_style) };
    Ok(())
}

#[pymethods]
impl AssStyle {
    // One argument per ASS_Style field libass reads; a subset would silently leave the rest of a
    // ported style table at zero.
    #[allow(clippy::too_many_arguments)]
    #[new]
    #[pyo3(signature = (
        *,
        name = String::from("Default"),
        font_name = String::from("sans-serif"),
        font_size = 0.0,
        primary_colour = 0,
        secondary_colour = 0,
        outline_colour = 0,
        back_colour = 0,
        bold = 0,
        italic = 0,
        underline = 0,
        strike_out = 0,
        scale_x = 1.0,
        scale_y = 1.0,
        spacing = 0.0,
        angle = 0.0,
        border_style = 1,
        outline = 0.0,
        shadow = 0.0,
        alignment = 2,
        margin_l = 0,
        margin_r = 0,
        margin_v = 0,
        encoding = 1,
        blur = 0.0,
        justify = 0,
    ))]
    fn new(
        name: String,
        font_name: String,
        font_size: f64,
        primary_colour: u32,
        secondary_colour: u32,
        outline_colour: u32,
        back_colour: u32,
        bold: i32,
        italic: i32,
        underline: i32,
        strike_out: i32,
        scale_x: f64,
        scale_y: f64,
        spacing: f64,
        angle: f64,
        border_style: i32,
        outline: f64,
        shadow: f64,
        alignment: i32,
        margin_l: i32,
        margin_r: i32,
        margin_v: i32,
        encoding: i32,
        blur: f64,
        justify: i32,
    ) -> PyResult<Self> {
        validate_scale("scale_x", scale_x)?;
        validate_scale("scale_y", scale_y)?;
        c_string("name", &name)?;
        c_string("font_name", &font_name)?;
        Ok(Self {
            name,
            font_name,
            font_size,
            primary_colour,
            secondary_colour,
            outline_colour,
            back_colour,
            bold,
            italic,
            underline,
            strike_out,
            scale_x,
            scale_y,
            spacing,
            angle,
            border_style,
            outline,
            shadow,
            alignment,
            margin_l,
            margin_r,
            margin_v,
            encoding,
            blur,
            justify,
        })
    }
}

#[pymethods]
impl RenderStyle {
    #[allow(clippy::too_many_arguments)]
    #[new]
    #[pyo3(signature = (
        *,
        font_scale = 1.0,
        line_spacing = 0.0,
        line_position = 0.0,
        hinting = 0,
        shaper = 1,
        override_bits = 0,
        override_style = None,
    ))]
    fn new(
        font_scale: f64,
        line_spacing: f64,
        line_position: f64,
        hinting: i32,
        shaper: i32,
        override_bits: i32,
        override_style: Option<Py<AssStyle>>,
    ) -> PyResult<Self> {
        validate_scale("font_scale", font_scale)?;
        validate_choice("hinting", hinting, 3)?;
        validate_choice("shaper", shaper, 1)?;
        if !(0.0..=100.0).contains(&line_position) {
            return Err(PyValueError::new_err(
                "line_position must be between 0 and 100",
            ));
        }
        if override_bits != 0 && override_style.is_none() {
            // A nonzero mask with no style overrides every selected field with a zeroed one.
            return Err(PyValueError::new_err(
                "override_bits requires an override_style",
            ));
        }
        Ok(Self {
            font_scale,
            line_spacing,
            line_position,
            hinting,
            shaper,
            override_bits,
            override_style,
        })
    }
}

#[pymethods]
impl AssRenderer {
    // Each argument is one libass call the host makes before a track can be parsed; they are
    // accepted together because libass is order-sensitive about them (see `open_native`).
    #[allow(clippy::too_many_arguments)]
    #[new]
    #[pyo3(signature = (
        ass,
        fonts=Vec::new(),
        *,
        library_path=None,
        fonts_dir=None,
        extract_fonts=false,
        default_font=None,
        default_family=None,
        font_provider=1,
        fontconfig_config=None,
        features=Vec::new(),
    ))]
    fn new(
        ass: Vec<u8>,
        fonts: Vec<(String, Vec<u8>)>,
        library_path: Option<PathBuf>,
        fonts_dir: Option<String>,
        extract_fonts: bool,
        default_font: Option<String>,
        default_family: Option<String>,
        font_provider: i32,
        fontconfig_config: Option<String>,
        features: Vec<(i32, bool)>,
    ) -> PyResult<Self> {
        let setup = FontSetup {
            fonts_dir,
            extract_fonts,
            default_font,
            default_family,
            font_provider,
            fontconfig_config,
        };
        Ok(Self {
            native: Mutex::new(Some(open_native(library_path, ass, fonts, setup, features)?)),
        })
    }

    #[pyo3(signature = (timestamp_ms, frame_size, storage_size, *, pixel_aspect=None, margins=(0, 0, 0, 0), use_margins=false, max_bitmap_bytes=None, style=None))]
    // The arity is libass's `ass_set_frame_size`/`_storage_size`/`_pixel_aspect`/`_margins` surface,
    // reached in one call because each is renderer state a caller must set together to get a frame.
    #[allow(clippy::too_many_arguments)]
    fn render(
        &self,
        py: Python<'_>,
        timestamp_ms: i64,
        frame_size: (i32, i32),
        storage_size: (i32, i32),
        pixel_aspect: Option<f64>,
        margins: (i32, i32, i32, i32),
        use_margins: bool,
        max_bitmap_bytes: Option<usize>,
        style: Option<Py<RenderStyle>>,
    ) -> PyResult<AssRenderResult> {
        validate_size("frame_size", frame_size)?;
        validate_size("storage_size", storage_size)?;
        validate_pixel_aspect(pixel_aspect)?;
        validate_margins(frame_size, margins)?;
        if max_bitmap_bytes == Some(0) {
            return Err(PyValueError::new_err("max_bitmap_bytes must be positive"));
        }
        let style = style.as_ref().map(|style| style.get());
        let (layers, detect_change) = py.detach(|| {
            let mut guard = self
                .native
                .lock()
                .map_err(|_| PyRuntimeError::new_err("renderer lock poisoned"))?;
            let native = guard
                .as_mut()
                .ok_or_else(|| PyRuntimeError::new_err("renderer is closed"))?;
            unsafe {
                (native.api.set_frame_size)(native.renderer, frame_size.0, frame_size.1);
                (native.api.set_storage_size)(native.renderer, storage_size.0, storage_size.1);
                (native.api.set_margins)(
                    native.renderer,
                    margins.0,
                    margins.1,
                    margins.2,
                    margins.3,
                );
                (native.api.set_use_margins)(native.renderer, i32::from(use_margins));
                (native.api.set_pixel_aspect)(native.renderer, pixel_aspect.unwrap_or(0.0));
            }
            apply_render_style(native, style)?;
            let mut detect_change = 0;
            let mut image = unsafe {
                (native.api.render_frame)(
                    native.renderer,
                    native.track,
                    timestamp_ms,
                    &mut detect_change,
                )
            };
            if !(0..=2).contains(&detect_change) {
                return Err(PyRuntimeError::new_err(
                    "libass returned an invalid change state",
                ));
            }
            let mut layers = Vec::new();
            let mut bitmap_bytes = 0usize;
            while !image.is_null() {
                let source = unsafe { &*image };
                let packed_len = bitmap_len(source).map_err(PyRuntimeError::new_err)?;
                bitmap_bytes = bitmap_bytes
                    .checked_add(packed_len)
                    .ok_or_else(|| PyRuntimeError::new_err("libass bitmap size overflow"))?;
                if max_bitmap_bytes.is_some_and(|limit| bitmap_bytes > limit) {
                    return Err(PyRuntimeError::new_err(format!(
                        "libass bitmap budget exceeded: {bitmap_bytes} > {}",
                        max_bitmap_bytes.unwrap_or_default()
                    )));
                }
                let bitmap =
                    unsafe { copy_bitmap(source, packed_len) }.map_err(PyRuntimeError::new_err)?;
                layers.push((
                    source.w,
                    source.h,
                    bitmap,
                    source.color,
                    source.dst_x,
                    source.dst_y,
                    source.image_type,
                ));
                image = source.next;
            }
            Ok((layers, detect_change))
        })?;
        let layers = layers
            .into_iter()
            .map(|(width, height, bitmap, color, dst_x, dst_y, image_type)| {
                Py::new(
                    py,
                    AssImageLayer {
                        width,
                        height,
                        stride: width,
                        bitmap: PyBytes::new(py, &bitmap).unbind(),
                        color,
                        dst_x,
                        dst_y,
                        image_type,
                    },
                )
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(AssRenderResult {
            layers,
            detect_change,
        })
    }

    fn close(&self) -> PyResult<()> {
        self.native
            .lock()
            .map_err(|_| PyRuntimeError::new_err("renderer lock poisoned"))?
            .take();
        Ok(())
    }

    fn library_version(&self) -> PyResult<u32> {
        let guard = self
            .native
            .lock()
            .map_err(|_| PyRuntimeError::new_err("renderer lock poisoned"))?;
        let native = guard
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("renderer is closed"))?;
        Ok(unsafe { (native.api.library_version)() } as u32)
    }

    fn library_path(&self) -> PyResult<String> {
        let guard = self
            .native
            .lock()
            .map_err(|_| PyRuntimeError::new_err("renderer lock poisoned"))?;
        let native = guard
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("renderer is closed"))?;
        Ok(native.library_path.clone())
    }

    /// Requested track features this libass build could not apply — `ASS_FEATURE_WRAP_UNICODE`
    /// without libunibreak, for instance. Reported rather than raised: the host's libass is the
    /// same build, so the same feature is missing on its side too, and the layouts still agree.
    fn unsupported_features(&self) -> PyResult<Vec<i32>> {
        let guard = self
            .native
            .lock()
            .map_err(|_| PyRuntimeError::new_err("renderer lock poisoned"))?;
        let native = guard
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("renderer is closed"))?;
        Ok(native.unsupported_features.clone())
    }
}

#[pyfunction]
#[pyo3(signature = (*, library_path=None))]
fn library_version(library_path: Option<PathBuf>) -> PyResult<u32> {
    let (library, _) = load_library(library_path).map_err(PyRuntimeError::new_err)?;
    let version: LibraryVersion =
        load_symbol(&library, b"ass_library_version\0").map_err(PyRuntimeError::new_err)?;
    let version = unsafe { version() } as u32;
    validate_version(version).map_err(PyRuntimeError::new_err)?;
    Ok(version)
}

#[pymodule(gil_used = false)]
fn libasslite(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<AssImageLayer>()?;
    module.add_class::<AssRenderResult>()?;
    module.add_class::<AssRenderer>()?;
    module.add_class::<AssStyle>()?;
    module.add_class::<RenderStyle>()?;
    module.add_function(wrap_pyfunction!(library_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_tested_patch_range() {
        assert!(validate_version(0x01701000).is_ok());
        assert!(validate_version(0x01705000).is_ok());
        assert!(validate_version(0x01700000).is_err());
        assert!(validate_version(0x01706000).is_err());
        assert!(validate_version(0x01602000).is_err());
        assert!(validate_version(0x01800000).is_err());
    }

    #[test]
    fn explicit_library_path_has_no_fallback_candidates() {
        let candidates = library_candidates(Some(PathBuf::from("chosen-libass"))).unwrap();

        assert_eq!(candidates, [OsString::from("chosen-libass")]);
    }

    #[test]
    fn zero_area_image_never_dereferences_its_bitmap() {
        let image = NativeImage {
            w: 0,
            h: 4,
            stride: 0,
            bitmap: ptr::null(),
            color: 0,
            dst_x: 0,
            dst_y: 0,
            next: ptr::null(),
            image_type: 0,
        };

        let packed_len = bitmap_len(&image).unwrap();
        assert_eq!(
            unsafe { copy_bitmap(&image, packed_len) }.unwrap(),
            Vec::<u8>::new()
        );
    }

    #[test]
    fn unknown_image_type_is_rejected_before_copy() {
        let image = NativeImage {
            w: 0,
            h: 0,
            stride: 0,
            bitmap: ptr::null(),
            color: 0,
            dst_x: 0,
            dst_y: 0,
            next: ptr::null(),
            image_type: 3,
        };

        assert_eq!(
            bitmap_len(&image).unwrap_err(),
            "libass returned an unknown image type"
        );
    }
}
