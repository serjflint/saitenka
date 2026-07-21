"""Saitenka overlay — rich-text + ruby renderer for the in-mpv Yomitan panel."""

from importlib.metadata import PackageNotFoundError, version

from overlay import fonts, render

try:  # the distribution is `saitenka-overlay`; the import package is `overlay`
    __version__ = version("saitenka-overlay")
except PackageNotFoundError:  # pragma: no cover — source tree without an installed dist
    __version__ = "0+unknown"

__all__ = ["__version__", "fonts", "render"]
