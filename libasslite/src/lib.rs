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

const MIN_LIBASS_VERSION: u32 = 0x01700000;
const NEXT_UNTESTED_LIBASS_VERSION: u32 = 0x01800000;
const LIBRARY_ENV: &str = "LIBASSLITE_LIBRARY";

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
type SetSize = unsafe extern "C" fn(*mut AssRendererHandle, c_int, c_int);
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
    render_frame: RenderFrame,
}

struct NativeRenderer {
    api: Api,
    library_handle: *mut AssLibrary,
    renderer: *mut AssRendererHandle,
    track: *mut AssTrack,
    library_path: String,
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

fn validate_version(version: u32) -> Result<(), String> {
    if (MIN_LIBASS_VERSION..NEXT_UNTESTED_LIBASS_VERSION).contains(&version) {
        Ok(())
    } else {
        Err(format!(
            "unsupported libass ABI 0x{version:08x}; expected 0.17.x"
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

unsafe fn copy_bitmap(source: &NativeImage) -> Result<Vec<u8>, &'static str> {
    if source.w < 0 || source.h < 0 || source.stride < source.w {
        return Err("libass returned invalid image dimensions");
    }
    if !(0..=2).contains(&source.image_type) {
        return Err("libass returned an unknown image type");
    }
    let width = usize::try_from(source.w).map_err(|_| "libass image is too large")?;
    let height = usize::try_from(source.h).map_err(|_| "libass image is too large")?;
    let stride = usize::try_from(source.stride).map_err(|_| "libass image is too large")?;
    let packed_len = width
        .checked_mul(height)
        .ok_or("libass image is too large")?;
    if packed_len == 0 {
        return Ok(Vec::new());
    }
    if source.bitmap.is_null() {
        return Err("libass returned a null bitmap");
    }
    let mut bitmap = vec![0; packed_len];
    for row in 0..height {
        let offset = row.checked_mul(stride).ok_or("libass image is too large")?;
        let src = unsafe { source.bitmap.add(offset) };
        let dst = &mut bitmap[row * width..(row + 1) * width];
        unsafe { ptr::copy_nonoverlapping(src, dst.as_mut_ptr(), width) };
    }
    Ok(bitmap)
}

fn open_native(
    explicit_path: Option<PathBuf>,
    mut ass: Vec<u8>,
    fonts: Vec<(String, Vec<u8>)>,
) -> PyResult<NativeRenderer> {
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
    let render_frame: RenderFrame =
        load_symbol(&library, b"ass_render_frame\0").map_err(PyRuntimeError::new_err)?;
    let version = unsafe { library_version() } as u32;
    validate_version(version).map_err(PyRuntimeError::new_err)?;

    let library_handle = unsafe { library_init() };
    if library_handle.is_null() {
        return Err(PyRuntimeError::new_err("ass_library_init failed"));
    }
    for (name, data, len) in fonts {
        unsafe { add_font(library_handle, name.as_ptr(), data.as_ptr().cast(), len) };
    }
    let renderer = unsafe { renderer_init(library_handle) };
    if renderer.is_null() {
        unsafe { library_done(library_handle) };
        return Err(PyRuntimeError::new_err("ass_renderer_init failed"));
    }
    unsafe { set_fonts(renderer, ptr::null(), ptr::null(), 1, ptr::null(), 0) };
    let track = unsafe {
        read_memory(
            library_handle,
            ass.as_mut_ptr().cast(),
            ass.len(),
            ptr::null(),
        )
    };
    if track.is_null() {
        unsafe {
            renderer_done(renderer);
            library_done(library_handle);
        }
        return Err(PyValueError::new_err("libass rejected the ASS document"));
    }
    Ok(NativeRenderer {
        api: Api {
            library_version,
            library_done,
            renderer_done,
            free_track,
            set_frame_size,
            set_storage_size,
            render_frame,
        },
        library_handle,
        renderer,
        track,
        library_path,
        _library: library,
    })
}

#[pymethods]
impl AssRenderer {
    #[new]
    #[pyo3(signature = (ass, fonts=Vec::new(), *, library_path=None))]
    fn new(
        ass: Vec<u8>,
        fonts: Vec<(String, Vec<u8>)>,
        library_path: Option<PathBuf>,
    ) -> PyResult<Self> {
        Ok(Self {
            native: Mutex::new(Some(open_native(library_path, ass, fonts)?)),
        })
    }

    fn render(
        &self,
        py: Python<'_>,
        timestamp_ms: i64,
        frame_size: (i32, i32),
        storage_size: (i32, i32),
    ) -> PyResult<AssRenderResult> {
        validate_size("frame_size", frame_size)?;
        validate_size("storage_size", storage_size)?;
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
            }
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
            while !image.is_null() {
                let source = unsafe { &*image };
                let bitmap = unsafe { copy_bitmap(source) }.map_err(PyRuntimeError::new_err)?;
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
    module.add_function(wrap_pyfunction!(library_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_only_tested_minor_line() {
        assert!(validate_version(0x01700000).is_ok());
        assert!(validate_version(0x01705000).is_ok());
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

        assert_eq!(unsafe { copy_bitmap(&image) }.unwrap(), Vec::<u8>::new());
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
            unsafe { copy_bitmap(&image) }.unwrap_err(),
            "libass returned an unknown image type"
        );
    }
}
