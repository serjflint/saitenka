"""Locators for bundled non-Python assets (fonts, wordlists, the mpv lua script).

These live under the ``overlay`` package (``src/overlay/assets``) so they ship in the wheel and
resolve via ``importlib.resources`` (see ``overlay.resources``) from an installed package, not just
the source tree.
"""

from __future__ import annotations

from pathlib import Path


def lua_path() -> Path:
    """Path to the bundled ``saitenka.lua`` mpv user-script."""
    from overlay.resources import asset

    return asset("saitenka.lua")
