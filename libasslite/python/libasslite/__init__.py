from __future__ import annotations

import os
from importlib import import_module
from typing import TYPE_CHECKING

from . import libasslite as _native

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike

AssImageLayer = _native.AssImageLayer
AssRenderResult = _native.AssRenderResult

_BUNDLE_ENV = "LIBASSLITE_BUNDLE"
_LIBRARY_ENV = "LIBASSLITE_LIBRARY"


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
    def __init__(
        self,
        ass: bytes,
        fonts: Sequence[tuple[str, bytes]] = (),
        *,
        library_path: str | PathLike[str] | None = None,
    ) -> None:
        self._native = _native.AssRenderer(
            ass,
            fonts,
            library_path=_selected_library(library_path),
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
    ) -> AssRenderResult:
        return self._native.render(
            timestamp_ms,
            frame_size,
            storage_size,
            pixel_aspect=pixel_aspect,
            margins=margins,
            use_margins=use_margins,
            max_bitmap_bytes=max_bitmap_bytes,
        )

    def close(self) -> None:
        self._native.close()

    def library_version(self) -> int:
        return self._native.library_version()

    def library_path(self) -> str:
        return self._native.library_path()


def library_version(*, library_path: str | PathLike[str] | None = None) -> int:
    return _native.library_version(library_path=_selected_library(library_path))


__all__ = ["AssImageLayer", "AssRenderResult", "AssRenderer", "library_version"]
