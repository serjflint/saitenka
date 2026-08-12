"""Locators for bundled non-Python assets (fonts, wordlists, the mpv lua script).

These live under the ``overlay`` package (``src/saitenka/assets``) so they ship in the wheel and
resolve via ``importlib.resources`` (see ``saitenka.resources``) from an installed package, not just
the source tree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def lua_path() -> Path:
    """Path to the bundled ``saitenka.lua`` mpv user-script."""
    from saitenka.resources import asset

    return asset("saitenka.lua")
