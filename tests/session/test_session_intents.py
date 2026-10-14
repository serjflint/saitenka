"""Session-wide commands are ordered decisions over current session facts."""

from __future__ import annotations

import pytest

from saitenka.app.intents import Announce, DismissHover
from saitenka.app.session.intents import (
    ReleaseSecondarySubtitles,
    ReportOverlayVisibility,
    ResumeSubtitles,
    SessionCommand,
    SessionInputs,
    SetSurfacesVisible,
    ShowTranslation,
    SuspendSubtitles,
    ToggleRenderer,
    reduce,
)

TOGGLE = SessionCommand.TOGGLE_OVERLAY


def test_hiding_tears_the_tooltip_down_before_the_pixels_go() -> None:
    """The tooltip is one of the surfaces about to vanish, and tearing it down afterwards resumes
    playback (hover auto-pause) against a session that has already gone dark."""
    assert reduce(TOGGLE, SessionInputs(overlay_visible=True)) == (
        DismissHover(),
        SetSurfacesVisible(visible=False),
        ReleaseSecondarySubtitles(),
        SuspendSubtitles(),
        ReportOverlayVisibility(visible=False),
    )


def test_showing_resumes_the_pipeline_before_drawing_a_translation() -> None:
    assert reduce(TOGGLE, SessionInputs(overlay_visible=False, translation_wanted=True)) == (
        SetSurfacesVisible(visible=True),
        ResumeSubtitles(),
        ShowTranslation(),
        ReportOverlayVisibility(visible=True),
    )


def test_showing_without_a_wanted_translation_draws_none() -> None:
    assert reduce(TOGGLE, SessionInputs(overlay_visible=False)) == (
        SetSurfacesVisible(visible=True),
        ResumeSubtitles(),
        ReportOverlayVisibility(visible=True),
    )


@pytest.mark.parametrize(("drawing_now", "engine"), [("native", "legacy"), ("legacy", "mpv's own")])
def test_the_renderer_toggle_names_the_engine_it_switches_to(drawing_now: str, engine: str) -> None:
    """The switch was silent. Its only tell on screen is that the legacy renderer paints a
    semi-transparent background box behind the cue — which a user has to already know to look for,
    and which says nothing at all if you were already on legacy. Every other toggle here announces."""
    effects = reduce(
        SessionCommand.TOGGLE_RENDERER, SessionInputs(legacy_forced=drawing_now == "legacy")
    )

    assert effects[0] == ToggleRenderer()
    assert Announce(f"subtitles: {engine} renderer") in effects
