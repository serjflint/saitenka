"""Public types for the compiled libasslite extension."""

from collections.abc import Sequence
from os import PathLike

type StrPath = str | PathLike[str]

class AssImageLayer:
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def stride(self) -> int: ...
    @property
    def bitmap(self) -> bytes: ...
    @property
    def color(self) -> int: ...
    @property
    def dst_x(self) -> int: ...
    @property
    def dst_y(self) -> int: ...
    @property
    def image_type(self) -> int: ...

class AssRenderResult:
    @property
    def layers(self) -> list[AssImageLayer]: ...
    @property
    def detect_change(self) -> int: ...

class AssStyle:
    def __init__(
        self,
        *,
        name: str = "Default",
        font_name: str = "sans-serif",
        font_size: float = 0.0,
        primary_colour: int = 0,
        secondary_colour: int = 0,
        outline_colour: int = 0,
        back_colour: int = 0,
        bold: int = 0,
        italic: int = 0,
        underline: int = 0,
        strike_out: int = 0,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        spacing: float = 0.0,
        angle: float = 0.0,
        border_style: int = 1,
        outline: float = 0.0,
        shadow: float = 0.0,
        alignment: int = 2,
        margin_l: int = 0,
        margin_r: int = 0,
        margin_v: int = 0,
        encoding: int = 1,
        blur: float = 0.0,
        justify: int = 0,
    ) -> None: ...
    @property
    def name(self) -> str: ...
    @property
    def font_name(self) -> str: ...
    @property
    def font_size(self) -> float: ...
    @property
    def primary_colour(self) -> int: ...
    @property
    def secondary_colour(self) -> int: ...
    @property
    def outline_colour(self) -> int: ...
    @property
    def back_colour(self) -> int: ...
    @property
    def bold(self) -> int: ...
    @property
    def italic(self) -> int: ...
    @property
    def underline(self) -> int: ...
    @property
    def strike_out(self) -> int: ...
    @property
    def scale_x(self) -> float: ...
    @property
    def scale_y(self) -> float: ...
    @property
    def spacing(self) -> float: ...
    @property
    def angle(self) -> float: ...
    @property
    def border_style(self) -> int: ...
    @property
    def outline(self) -> float: ...
    @property
    def shadow(self) -> float: ...
    @property
    def alignment(self) -> int: ...
    @property
    def margin_l(self) -> int: ...
    @property
    def margin_r(self) -> int: ...
    @property
    def margin_v(self) -> int: ...
    @property
    def encoding(self) -> int: ...
    @property
    def blur(self) -> float: ...
    @property
    def justify(self) -> int: ...

class RenderStyle:
    def __init__(
        self,
        *,
        font_scale: float = 1.0,
        line_spacing: float = 0.0,
        line_position: float = 0.0,
        hinting: int = 0,
        shaper: int = 1,
        override_bits: int = 0,
        override_style: AssStyle | None = None,
    ) -> None: ...
    @property
    def font_scale(self) -> float: ...
    @property
    def line_spacing(self) -> float: ...
    @property
    def line_position(self) -> float: ...
    @property
    def hinting(self) -> int: ...
    @property
    def shaper(self) -> int: ...
    @property
    def override_bits(self) -> int: ...
    @property
    def override_style(self) -> AssStyle | None: ...

class AssRenderer:
    def __init__(
        self,
        ass: bytes,
        fonts: Sequence[tuple[str, bytes]] = (),
        *,
        library_path: StrPath | None = None,
        fonts_dir: str | None = None,
        extract_fonts: bool = False,
        default_font: str | None = None,
        default_family: str | None = None,
        font_provider: int = 1,
        fontconfig_config: str | None = None,
        features: Sequence[tuple[int, bool]] = (),
    ) -> None: ...
    def render(
        self,
        timestamp_ms: int,
        frame_size: tuple[int, int],
        storage_size: tuple[int, int],
        *,
        pixel_aspect: float | None = None,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        use_margins: bool = False,
        max_bitmap_bytes: int | None = None,
        style: RenderStyle | None = None,
    ) -> AssRenderResult: ...
    def close(self) -> None: ...
    def library_version(self) -> int: ...
    def library_path(self) -> str: ...
    def unsupported_features(self) -> list[int]: ...

def library_version(*, library_path: StrPath | None = None) -> int: ...
