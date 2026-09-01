"""Subtitle navigation and feature-owned state projections.

The behavioural contract: episode-scoped state lives in one swappable object, and rebinding it resets
*all* of that state in a single move (no field can leak into the next episode). That leak-freedom is
exactly what the future file-change re-slot relies on, so it is asserted here directly.
"""

from __future__ import annotations

import util
from saitenka_subtitles import Cue

from saitenka.app.features.subtitle.navigation_state import NavigationState
from saitenka.app.features.tooltip.popups import PopupView, TooltipState
from saitenka.runtime.events import SubtitleSecondaryLeased, SubtitleStartupConfigured


class FakeIPC(util.FakeIPC):
    """Minimal mpv IPC stand-in — enough to build a SessionController."""


def test_navigation_state_defaults_are_the_no_source_state():
    ctx = NavigationState()
    assert (
        ctx.sub_index,
        ctx.nav_idx,
        ctx.sub_settle.open,
        ctx.nav_prev_text,
        ctx.nav_provisional_cue_counted,
    ) == (
        None,
        -1,
        False,
        "",
        False,
    )


def test_reader_delegates_episode_fields_to_the_context(make_session):
    r = make_session(FakeIPC())
    assert r.graph.track_commands.navigation.current.nav_idx == -1
    r.graph.track_commands.navigation.current.nav_idx = 7
    assert r.graph.track_commands.navigation.current.nav_idx == 7


def test_reslot_rebinds_the_episode_without_leaking_prior_state(make_session):
    r = make_session(FakeIPC())
    r.graph.track_commands.navigation.current.nav_idx = 9
    r.graph.track_commands.navigation.current.sub_settle = (
        r.graph.track_commands.navigation.current.sub_settle.begin()
    )
    # A geometry hint names a cue of *this* file, so carrying one over would aim the next episode's
    # first decision at a line that is nowhere in it. It was a SessionController field, where nothing cleared it.
    r.graph.track_commands.navigation.current.geometry_cue_hint = Cue(1.0, 2.0, "犬")

    r.graph.track_commands.navigation.replace(NavigationState())

    assert r.graph.track_commands.navigation.current.nav_idx == -1
    assert r.graph.track_commands.navigation.current.sub_settle.open is False
    assert r.graph.track_commands.navigation.current.geometry_cue_hint is None


def test_the_track_selection_is_reset_by_configuring_it_not_by_the_rebind(make_session):
    """`Owner.SUBTITLE`'s slice is session-lived, so the episode rebind cannot clear it.

    What keeps it episode-safe is that a re-slot always configures the new file's tracks, and that
    declaration is a whole-state reset. Asserted here because it is the one episode fact the
    leak-free-by-construction rebind no longer covers.
    """
    r = make_session(FakeIPC())
    r.graph.track_commands.declare(SubtitleStartupConfigured(5, 6, "en", "ja,jpn,jp"))
    r.graph.track_commands.declare(SubtitleSecondaryLeased(6))

    r.graph.track_commands.navigation.replace(NavigationState())
    assert (
        r.graph.track_commands.current().jp_sid,
        r.graph.track_commands.current().en_sid,
        r.graph.track_commands.current().language,
    ) == (5, 6, "en")  # the rebind does not reach it

    r.graph.track_commands.declare(SubtitleStartupConfigured(None, None, "jp", "ja,jpn,jp"))
    assert (
        r.graph.track_commands.current().jp_sid,
        r.graph.track_commands.current().en_sid,
        r.graph.track_commands.current().language,
    ) == (None, None, "jp")
    assert (
        r.graph.track_commands.current().secondary_sid is None
    )  # the lease goes with the selection


def test_tooltip_controller_owns_its_surface_state(make_session):
    r = make_session(FakeIPC())
    assert isinstance(r.graph.tooltip.surface_state(), TooltipState)
    assert (
        r.graph.tooltip.surface_state().nest is r.graph.tooltip.surface_state().nest
        and isinstance(r.graph.tooltip.surface_state().nest, PopupView)
    )
    r.graph.tooltip.surface_state().view.scroll = 4
    r.graph.tooltip.surface_state().hover_reading = "よむ"
    assert (
        r.graph.tooltip.surface_state().view.scroll == 4
        and r.graph.tooltip.surface_state().hover_reading == "よむ"
    )


def test_session_does_not_project_tooltip_state_twice(make_session):
    r = make_session(FakeIPC())

    assert not hasattr(r, "tip")


def test_feature_owned_session_state_survives_an_episode_reslot(make_session):
    r = make_session(FakeIPC())
    r.graph.mining.record_mined_expression("読む")
    backlog = object()
    r.graph.history.replace_backlog(backlog)  # type: ignore[assignment]  # lifetime sentinel
    history_before = r.graph.history

    r.graph.track_commands.navigation.replace(NavigationState())

    assert r.graph.history is history_before
    assert "読む" in r.graph.mining.index_snapshot()
    assert r.graph.history.backlog is backlog
