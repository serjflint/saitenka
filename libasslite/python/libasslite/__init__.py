from __future__ import annotations

import os
from enum import IntEnum, IntFlag
from importlib import import_module
from typing import TYPE_CHECKING

from . import libasslite as _native

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike

AssImageLayer = _native.AssImageLayer
AssRenderResult = _native.AssRenderResult
AssStyle = _native.AssStyle
RenderStyle = _native.RenderStyle

_BUNDLE_ENV = "LIBASSLITE_BUNDLE"
_LIBRARY_ENV = "LIBASSLITE_LIBRARY"


class FontProvider(IntEnum):
    """`ASS_DefaultFontProvider`. `NONE` confines lookup to what the caller loaded."""

    NONE = 0
    AUTODETECT = 1
    CORETEXT = 2
    FONTCONFIG = 3
    DIRECTWRITE = 4


class Hinting(IntEnum):
    """`ASS_Hinting`. Anything but `NONE` conflicts with smooth scaling and positioning."""

    NONE = 0
    LIGHT = 1
    NORMAL = 2
    NATIVE = 3


class Shaping(IntEnum):
    """`ASS_ShapingLevel`."""

    SIMPLE = 0
    COMPLEX = 1


class Feature(IntEnum):
    """`ASS_Feature`, as a track flag. A host applies these to converted (non-ASS) tracks."""

    INCOMPATIBLE_EXTENSIONS = 0
    BIDI_BRACKETS = 1
    WHOLE_TEXT_LAYOUT = 2
    WRAP_UNICODE = 3


class OverrideBits(IntFlag):
    """`ASS_OverrideBits`, the mask selective style override applies."""

    DEFAULT = 0
    STYLE = 1 << 0
    SELECTIVE_FONT_SCALE = 1 << 1
    FONT_SIZE_FIELDS = 1 << 2
    FONT_NAME = 1 << 3
    COLORS = 1 << 4
    ATTRIBUTES = 1 << 5
    BORDER = 1 << 6
    ALIGNMENT = 1 << 7
    MARGINS = 1 << 8
    FULL_STYLE = 1 << 9
    JUSTIFY = 1 << 10
    BLUR = 1 << 11


def _bundle_library() -> str | None:
    if os.environ.get(_LIBRARY_ENV) is not None:
        return None
    if os.environ.get(_BUNDLE_ENV, "1").casefold() in {"0", "false", "no", "off"}:
        return None
    try:
        bundle = import_module("libasslite_bundle")
    except ModuleNotFoundError as error:
        if error.name != "libasslite_bundle":
            raise
        return None
    return os.fspath(bundle.library_path())


def _selected_library(library_path: str | PathLike[str] | None) -> str | PathLike[str] | None:
    return library_path if library_path is not None else _bundle_library()


class AssRenderer:
    # PLR0913 is suppressed because each argument is one order-sensitive libass call made before the
    # track is parsed: an options object would hide that they are a single init sequence, and
    # setters would expose the ordering.
    def __init__(  # noqa: PLR0913
        self,
        ass: bytes,
        fonts: Sequence[tuple[str, bytes]] = (),
        *,
        library_path: str | PathLike[str] | None = None,
        fonts_dir: str | None = None,
        extract_fonts: bool = False,
        default_font: str | None = None,
        default_family: str | None = None,
        font_provider: int = FontProvider.AUTODETECT,
        fontconfig_config: str | None = None,
        features: Sequence[tuple[int, bool]] = (),
    ) -> None:
        self._native = _native.AssRenderer(
            ass,
            fonts,
            library_path=_selected_library(library_path),
            fonts_dir=fonts_dir,
            extract_fonts=extract_fonts,
            default_font=default_font,
            default_family=default_family,
            font_provider=int(font_provider),
            fontconfig_config=fontconfig_config,
            features=features,
        )

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
    ) -> AssRenderResult:
        return self._native.render(
            timestamp_ms,
            frame_size,
            storage_size,
            pixel_aspect=pixel_aspect,
            margins=margins,
            use_margins=use_margins,
            max_bitmap_bytes=max_bitmap_bytes,
            style=style,
        )

    def close(self) -> None:
        self._native.close()

    def library_version(self) -> int:
        return self._native.library_version()

    def library_path(self) -> str:
        return self._native.library_path()

    def unsupported_features(self) -> list[int]:
        return self._native.unsupported_features()


def library_version(*, library_path: str | PathLike[str] | None = None) -> int:
    return _native.library_version(library_path=_selected_library(library_path))


__all__ = [
    "AssImageLayer",
    "AssRenderResult",
    "AssRenderer",
    "AssStyle",
    "Feature",
    "FontProvider",
    "Hinting",
    "OverrideBits",
    "RenderStyle",
    "Shaping",
    "library_version",
]
