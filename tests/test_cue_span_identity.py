"""The cue handle's own properties. The gate that every joinable span carries it is in
`test_native_subtitles.py`, where the harness drives geometry."""

from __future__ import annotations

from saitenka.app.subtitle_geometry_diagnostics import cue_digest


def test_the_cue_handle_is_stable_for_one_text() -> None:
    """A join key that varied per call would silently split one cue into many."""
    assert cue_digest("犬…　かな？") == cue_digest("犬…　かな？")
    assert cue_digest("犬…　かな？") != cue_digest("♬～")


def test_the_handle_never_carries_the_subtitle_text() -> None:
    """A span attribute has no cardinality limit, but a subtitle line is the user\'s content and a
    bundle gets shared. The digest is what makes stamping every span safe."""
    text = "犬…　かな？"

    assert text not in cue_digest(text)
    assert len(cue_digest(text)) == 8
