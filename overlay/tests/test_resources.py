"""Stage 17a: bundled assets resolve via importlib.resources from an INSTALLED wheel, not just the
source tree. The resolver must find fonts, wordlists (jlpt.zip), and saitenka.lua whether we're
running from ``src/`` or an unpacked wheel.
"""

from __future__ import annotations

from overlay import resources


def test_font_files_resolve():
    for name in ("NotoSansJP.ttf", "NotoSans.ttf"):
        p = resources.asset("fonts", name)
        assert p.exists(), p
        assert p.stat().st_size > 0


def test_wordlist_zip_resolves():
    p = resources.asset("wordlists", "jlpt.zip")
    assert p.exists() and p.stat().st_size > 0


def test_lua_resolves():
    p = resources.asset("saitenka.lua")
    assert p.exists()
    assert "saitenka-overlay" in p.read_text(encoding="utf-8")


def test_existing_loaders_use_the_resolver():
    """fonts / wordlists must load through the resolver so the wheel path works too."""
    from overlay import fonts
    from overlay.app import wordlists

    assert fonts.ASSETS.exists()
    assert wordlists.JLPT_ZIP.exists()
