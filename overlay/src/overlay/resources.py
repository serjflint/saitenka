"""Locate bundled non-Python assets via ``importlib.resources``.

All data files (fonts, wordlists, ``saitenka.lua``) live UNDER the
``overlay`` package (``src/overlay/assets`` and ``src/overlay/app/data``), so they resolve the same
way from the source tree and from an installed wheel. ``importlib.resources.files`` returns a real
filesystem path for wheels that uv unpacks; the loaders below therefore get a ``Path`` usable by
Pillow / zipfile / ``open`` directly.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def _pkg_path(package: str, *parts: str) -> Path:
    """Filesystem path to a data file under ``package`` (source tree or unpacked wheel)."""
    root = resources.files(package)
    child = root.joinpath(*parts)
    # our wheels are unpacked on install (uv tool install), so this is always a real path
    return Path(str(child))


def asset(*parts: str) -> Path:
    """Path to a file under ``overlay/assets`` (e.g. ``asset("fonts", "NotoSansJP.ttf")``)."""
    return _pkg_path("overlay", "assets", *parts)


def data(*parts: str) -> Path:
    """Path to a file under ``overlay/app/data`` (source tree or unpacked wheel)."""
    return _pkg_path("overlay.app", "data", *parts)
