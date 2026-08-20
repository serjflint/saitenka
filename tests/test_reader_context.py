"""EpisodeContext composition + Reader delegation — the #30 lifetime split and the #100 re-slot seam.

The behavioural contract: episode-scoped state lives in one swappable object, and rebinding it resets
*all* of that state in a single move (no field can leak into the next episode). That leak-freedom is
exactly what the future file-change re-slot relies on, so it is asserted here directly.
"""

from __future__ import annotations

import util

from saitenka.app.controller import Reader
from saitenka.app.popups import HoverMetadata, PopupView, TooltipState
from saitenka.app.reader_context import EpisodeContext
from saitenka.runtime.events import SubtitleSecondaryLeased, SubtitleStartupConfigured
from saitenka.subtitles import Cue


class FakeIPC(util.FakeIPC):
    """Minimal mpv IPC stand-in — enough to build a Reader."""


def test_episode_context_defaults_are_the_no_episode_state():
    ctx = EpisodeContext()
    assert (ctx.subtitle.retry_factory, ctx.subtitle.retry_active) == (None, False)
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


def test_reader_delegates_episode_fields_to_the_context():
    r = Reader(FakeIPC())
    # a nested field (episode.subtitle) and a direct one (episode) both read through…
    assert r.episode.subtitle.retry_active is False
    assert r.episode.nav_idx == r.episode.nav_idx == -1
    # …and writes land on the owning context, under both the public and historical private names
    r.episode.subtitle.retry_active = True
    r.episode.nav_idx = 7
    assert (r.episode.subtitle.retry_active, r.episode.nav_idx) == (True, 7)


def test_reslot_rebinds_the_episode_without_leaking_prior_state():
    r = Reader(FakeIPC())
    r.episode.nav_idx = 9
    r.episode.sub_settle = r.episode.sub_settle.begin()
    r.episode.subtitle.retry_active = True  # a nested-cluster field, migrated fully off the Reader
    # A geometry hint names a cue of *this* file, so carrying one over would aim the next episode's
    # first decision at a line that is nowhere in it. It was a Reader field, where nothing cleared it.
    r.episode.geometry_cue_hint = Cue(1.0, 2.0, "犬")

    r.episode = EpisodeContext()  # the re-slot move: one rebind resets every episode field

    assert r.episode.nav_idx == -1
    assert r.episode.sub_settle.open is False
    assert r.episode.subtitle.retry_active is False
    assert r.episode.geometry_cue_hint is None


def test_the_track_selection_is_reset_by_configuring_it_not_by_the_rebind():
    """`Owner.SUBTITLE`'s slice is session-lived, so the episode rebind cannot clear it.

    What keeps it episode-safe is that a re-slot always configures the new file's tracks, and that
    declaration is a whole-state reset. Asserted here because it is the one episode fact the
    leak-free-by-construction rebind no longer covers.
    """
    r = Reader(FakeIPC())
    r.declare_subtitle(SubtitleStartupConfigured(5, 6, "en", "ja,jpn,jp"))
    r.declare_subtitle(SubtitleSecondaryLeased(6))

    r.episode = EpisodeContext()
    assert (r.jp_sid, r.en_sid, r.subtitle_language) == (5, 6, "en")  # the rebind does not reach it

    r.declare_subtitle(SubtitleStartupConfigured(None, None, "jp", "ja,jpn,jp"))
    assert (r.jp_sid, r.en_sid, r.subtitle_language) == (None, None, "jp")
    assert r._translation_secondary_sid is None  # the lease goes with the selection


def test_reader_delegates_tooltip_fields_to_tip_state():
    r = Reader(FakeIPC())
    assert isinstance(r.tip, TooltipState)
    # the historical private names read/write through the grouped state, incl. the nested-popup handle
    assert r.tip.nest is r.tip.nest and isinstance(r.tip.nest, PopupView)
    r.tip.view.scroll = 4
    r.tip.hover_reading = "よむ"
    # base view-state fields live on the shared PopupView (tip.view); FSM fields stay flat on TooltipState
    assert r.tip.view.scroll == 4 and r.tip.hover_reading == "よむ"


def test_rebinding_tip_resets_the_whole_hover_stack():
    """Tearing down / re-slotting the tooltip is one rebind: every hover-FSM field (shown panel, scroll,
    scan/word dwell, hovered-word metadata) returns to its no-hover default in a single move,
    leak-free by construction — the same property the episode re-slot relies on, one tier down."""
    r = Reader(FakeIPC())
    r.tip.view.rect = (1, 2, 3, 4)
    r.tip.view.scroll = 9
    r.tip.scan_target = "cell"
    r.tip.word_target = 2
    r.tip.hover = HoverMetadata(terms=("数ある",))
    r.tip.kanji_index = 3

    r.tip = TooltipState()  # the teardown/re-slot move

    assert r.tip.view.rect is None
    assert r.tip.view.scroll == 0
    assert r.tip.scan_target is None
    assert r.tip.word_target is None
    assert r.tip.hover.terms == ()
    assert r.tip.kanji_index == 0


def test_session_state_survives_an_episode_reslot():
    """The other half of the lifetime contract: session-scoped state (the deck-mined set, the Anki
    reachability cache, the backlog handle) is durable — an episode swap must NOT reset it, or #100's
    re-slot would forget what's already in the deck on every file change."""
    r = Reader(FakeIPC())
    r.session.mined.add("読む")
    r.session.anki_cache = (123.0, True)
    session_before = r.session

    r.episode = EpisodeContext()  # advance to the next file

    assert r.session is session_before  # same session object — not rebound
    assert "読む" in r.session.mined  # deck knowledge carried across the episode boundary
    assert r.session.anki_cache == (123.0, True)
