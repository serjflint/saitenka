"""The cue handle is the join key across cue and geometry spans.

Before this, exactly one span in the chain carried a cue identity. The rest used `generation`,
`cue_revision`, `timestamp_ms`, or nothing — four vocabularies with no shared key — so relating a
draw to the decision that produced it meant a timestamp window. That technique produced three
confident wrong mechanisms in one session while `tokens=0, measured_boxes=3`, the pair that named
the actual defect, sat on a span nobody could join to.

The gate that every joinable span carries it lives in `test_native_subtitles.py`, where the harness
actually drives geometry; these are the handle's own properties.
"""

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
