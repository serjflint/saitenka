"""The cue handle's own properties. The gate that every joinable span carries it is in
`test_native_subtitles.py`, where the harness drives geometry."""

from __future__ import annotations

from saitenka.app.subtitle_geometry_diagnostics import cue_digest


def test_the_cue_handle_is_stable_distinct_and_private() -> None:
    text = "犬…　かな？"
    handle = cue_digest(text)
    assert handle == cue_digest(text)
    assert handle != cue_digest("♬～")
    assert text not in handle
    assert len(handle) == 8
    assert all(ch in "0123456789abcdef" for ch in handle)
