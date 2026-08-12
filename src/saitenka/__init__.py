"""Saitenka Japanese immersion tooling."""

from importlib.metadata import PackageNotFoundError, version

from saitenka import fonts, render

try:
    __version__ = version("saitenka")
except PackageNotFoundError:  # pragma: no cover — source tree without an installed dist
    __version__ = "0+unknown"

__all__ = ["__version__", "fonts", "render"]
