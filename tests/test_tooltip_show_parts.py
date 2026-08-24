"""The two pieces of the tooltip show that need no host.

`show_tooltip_impl` was a 120-line function touching 38 host members and writing 16 of them, which
made every one of its decisions reachable only by driving a whole SessionController. These two are the parts
that were never about a host at all — freezing the frame and placing the panel — and they are worth
testing directly because the bugs they can carry are arithmetic, not integration.
"""

from __future__ import annotations

import pytest
from util import FakeIPC

from saitenka.app.tooltip import _freeze_frame
from saitenka.app.tooltip_panel import place_tip


def _paused(ipc: FakeIPC) -> list[tuple]:
    """The pause writes this fake actually saw — `send_correlated` completes inline through it."""
    return [c for c in ipc.commands if c[:2] == ("set_property", "pause")]


class _View:
    def __init__(self) -> None:
        self.scroll = 7
        self.desired_scroll = 7
        self.view_h = 0
        self.xy = None


def test_a_hover_pauses_a_playing_video() -> None:
    ipc = FakeIPC()
    assert _freeze_frame(ipc, lambda _p: False, enabled=True, already_paused=False) is True
    assert _paused(ipc)  # it actually issued the pause


def test_a_hover_does_not_re_pause_what_it_already_paused() -> None:
    """Reporting a second pause would let the resume-on-hide bookkeeping unpause a user's own pause."""
    ipc = FakeIPC()
    assert _freeze_frame(ipc, lambda _p: True, enabled=True, already_paused=True) is False
    assert _paused(ipc) == []


def test_a_hover_never_pauses_a_video_the_user_already_paused() -> None:
    """The distinction that matters: WE paused it, versus it was already paused.

    Returning True here would make the hide path resume playback the user had deliberately stopped.
    """
    ipc = FakeIPC()
    assert _freeze_frame(ipc, lambda _p: True, enabled=True, already_paused=False) is False
    assert _paused(ipc) == []


def test_pause_on_tooltip_off_means_no_pause_at_all() -> None:
    ipc = FakeIPC()
    assert _freeze_frame(ipc, lambda _p: False, enabled=False, already_paused=False) is False
    assert _paused(ipc) == []


def test_placing_a_panel_resets_the_scroll_of_the_previous_word() -> None:
    """A new word starts at the top: carrying the last word's scroll shows its middle."""
    view = _View()
    place_tip(view, 300, 900, 400, (10, 20, 30), scale=1.0, osd=(1920, 1080))
    assert (view.scroll, view.desired_scroll) == (0, 0)


def test_a_tall_entry_is_capped_rather_than_fitted() -> None:
    """It scrolls, so the cap wins — fitting a 900px entry would spill under the window chrome."""
    view = _View()
    place_tip(view, 300, 900, 400, (10, 20, 30), scale=1.0, osd=(1920, 1080))
    assert view.view_h == 400


def test_a_short_entry_uses_its_own_height() -> None:
    """The negative control for the cap: it must not pad a short panel out to the ceiling."""
    view = _View()
    place_tip(view, 300, 120, 400, (10, 20, 30), scale=1.0, osd=(1920, 1080))
    assert view.view_h == 120


def test_placement_lands_inside_the_screen() -> None:
    """Anchored at the far corner — the safe area is the whole reason placement is not just (wx, wy)."""
    view = _View()
    place_tip(view, 300, 200, 400, (1900, 1060, 30), scale=1.0, osd=(1920, 1080))
    assert view.xy is not None
    x, y = view.xy
    assert 0 <= x <= 1920 - 300
    assert 0 <= y <= 1080


def test_a_panel_build_needs_no_host() -> None:
    """`_build_panel` used to read eleven SessionController attributes mid-render.

    It now takes a `PanelStyle` snapshotted at the boundary, so a build cannot observe the config
    changing underneath it — the same reason `DrawRequest` is frozen. That it constructs at all with
    no SessionController in scope is the assertion.
    """
    from saitenka.app.tokenize import Token
    from saitenka.app.tooltip_panel import PanelKey, PanelStyle, _build_panel
    from saitenka.panel import Definition, Entry

    class _DictSet:
        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002  # protocol shape
            return Entry(headword=tok.surface, defs=[Definition("D", ["x"])])

    style = PanelStyle(
        width=420,
        band_cache_max=4,
        raw_band_ceiling=8,
        layout_backend=None,
        layout_engine="taffy",
        add_button=False,
        speak_button=False,
        dict_set=_DictSet(),
    )
    token = Token(surface="猫", lemma="猫", reading="ねこ", pos="名詞", start=0, end=1)
    key = PanelKey(
        lemma="猫",
        surface="猫",
        reading="ねこ",
        inflected="猫",
        width=420,
        anki_ok=False,
        mined=False,
        tts_ok=False,
        group_mined=(),
        phrase_terms=(),
    )

    panel = _build_panel(style, key, token, "猫", mined=False)

    assert panel.width == 420  # the style's width, not a host's


def test_the_style_is_frozen_so_a_build_cannot_see_it_change() -> None:
    from dataclasses import FrozenInstanceError

    from saitenka.app.tooltip_panel import PanelStyle

    style = PanelStyle(
        width=420,
        band_cache_max=4,
        raw_band_ceiling=8,
        layout_backend=None,
        layout_engine="taffy",
        add_button=False,
        speak_button=False,
    )
    with pytest.raises(FrozenInstanceError):
        style.width = 900  # type: ignore[misc]
