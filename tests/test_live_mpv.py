"""L3 real-mpv smoke — inject REAL mouse/key events into a LIVE mpv and verify the overlay reacts.

Opt-in (needs a real display): ``SAITENKA_LIVE=1`` — `uv run poe smoke-live`. Skipped in the normal
gate. This is the only layer that exercises mpv's ``mouse-pos`` → OSD coordinate mapping: the
HiDPI/Retina hit-alignment (R1) the headless FakeIPC tests structurally can't reach, because they set
``mouse-pos`` directly in OSD coords. It drives mpv's own ``mouse`` / ``keypress`` input commands and
saves a screenshot artifact.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest
from live_harness import live_reader as _live_reader
from live_harness import poll_until as _poll_until
from PIL import Image, ImageChops

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1 (needs a display); run `uv run poe smoke-live`",
)


def _screenshot(ipc, path: Path) -> Image.Image:
    response = ipc.command("screenshot-to-file", str(path), "window")
    assert response.get("error") == "success"
    return Image.open(path).convert("RGB")


def _targets_for(reader, x, y):
    """The live check's view of `_hover_targets`, wired from the reader it is probing."""
    from saitenka.app.features.tooltip.tooltip import _hover_targets

    return _hover_targets(
        x,
        y,
        inside=True,
        tip_rect=reader.graph.tooltip.surface_state().view.rect,
        nest_rect=reader.graph.tooltip.surface_state().nest.rect,
        hit=lambda hx, hy: (
            reader.graph.tooltip.hit(hx, hy)
            if reader.graph.subtitle_presentation.cue.current.tokens
            else -1
        ),
    )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_real_mouse_shows_tooltip_on_the_aimed_word():
    with _live_reader() as (tmp, reader, ipc):
        # aim a REAL mouse move at the screen centre of a content word
        i = next(
            k
            for k, t in enumerate(reader.graph.subtitle_presentation.cue.current.tokens)
            if t.is_content
        )
        box = next(b for b in reader.graph.subtitle_presentation.cue.current.boxes if b.index == i)
        ox, oy = reader.graph.subtitle_presentation.cue.current.origin
        cx, cy = int(ox + box.x + box.w / 2), int(oy + box.y + box.h / 2)
        ipc.command("mouse", cx, cy)

        _poll_until(
            reader,
            lambda: reader.graph.tooltip.surface_state().view.rect is not None,
            "a real mouse over a word did not show a tooltip",
        )
        ipc.command("screenshot-to-file", str(tmp / "live_hover.png"), "window")

        # R1: the hovered word must be the one we aimed at — this is the mouse-pos→OSD alignment the
        # headless tests can't check. A mismatch here is the HiDPI scaling bug.
        assert reader.graph.tooltip.observation().selected == i, (
            f"hover misaligned: aimed word {i} ({reader.graph.subtitle_presentation.cue.current.tokens[i].surface!r}), "
            f"got {reader.graph.tooltip.observation().selected} — mouse-pos→OSD mapping (HiDPI/R1)? screenshot: {tmp / 'live_hover.png'}"
        )

        # a real keypress must reach the reader (mine key is bound) — drive it and drain
        reader.pump()


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_cursor_over_tooltip_keeps_lease_and_captures_click():
    """Occlusion + click capture through the REAL mouse-pos→OSD path: with the cursor moved onto the
    shown tooltip, the base hit-test must report over_tip (keep the lease, don't hijack to a word
    under it), and a real left-click on the tooltip body must be captured (pause lease retained), not
    fall through to mpv. Regression guard for the windowed-renderer _tip_rect calc."""
    from saitenka.panel import Definition, Entry

    class _TallDS:
        """A tall, scrollable entry so the windowed renderer's full_height is a CONVERGING ESTIMATE
        (not the one-line _MiniDS where it's exact) — the case the user hits with real dictionaries."""

        def entry_for(self, tok, inflected=None, *, extra_terms=()):  # noqa: ARG002
            para = "とても長い定義の本文で" * 8
            return Entry(
                headword=[tok.surface],
                reading=getattr(tok, "reading", "") or tok.surface,
                defs=[Definition(f"辞書{i}", [para]) for i in range(6)],
            )

        def has_term(self, *_forms):
            return False

    with _live_reader() as (_tmp, reader, ipc):
        reader.graph.profile.profile.replace_dictionary_set(
            _TallDS()
        )  # rebuild the next hover's panel as a tall, scrolling tip
        reader.graph.tooltip.surface_state().panel_cache.clear()
        i = next(
            k
            for k, t in enumerate(reader.graph.subtitle_presentation.cue.current.tokens)
            if t.is_content
        )
        box = next(b for b in reader.graph.subtitle_presentation.cue.current.boxes if b.index == i)
        ox, oy = reader.graph.subtitle_presentation.cue.current.origin
        ipc.command("mouse", int(ox + box.x + box.w / 2), int(oy + box.y + box.h / 2))
        _poll_until(
            reader,
            lambda: reader.graph.tooltip.surface_state().view.rect is not None,
            "hover did not show a tooltip",
        )

        # occlusion calc: the dead centre of the rendered tooltip must read over_tip (keep the lease),
        # NOT a word beneath it. This checks the windowed-renderer _tip_rect against the same OSD
        # coordinate space mpv reports mouse-pos in — the alignment the headless fakes can't see.
        tx, ty, tw, th = reader.graph.tooltip.surface_state().view.rect
        cx, cy = int(tx + tw / 2), int(ty + th / 2)
        over_word, over_tip, _nest = _targets_for(reader, cx, cy)
        assert over_tip and over_word == -1, (
            f"cursor over the tooltip must read over_tip (occlusion); got over_tip={over_tip} "
            f"over_word={over_word} — _tip_rect={reader.graph.tooltip.surface_state().view.rect} (windowed-renderer rect calc?)"
        )

        # real cursor onto the tooltip → the lease holds (hover stays on the aimed word, not hijacked)
        ipc.command("mouse", cx, cy)
        for _ in range(5):
            reader.pump()
            time.sleep(0.02)
        assert reader.graph.tooltip.observation().selected == i, (
            f"resting on the tooltip must keep its lease; hover={reader.graph.tooltip.observation().selected} aimed={i} "
            f"(_tip_rect={reader.graph.tooltip.surface_state().view.rect})"
        )

        # a real left-click on the tooltip body must be captured (tip stays), not fall through
        ipc.command("keypress", "MBTN_LEFT")
        for _ in range(5):
            reader.pump()
            time.sleep(0.02)
        assert reader.graph.tooltip.surface_state().view.rect is not None, (
            "a click on the tooltip must be captured, not tear it down"
        )

        # after scrolling the tall tip (windowed full_height converges here), the rect must still
        # match the drawn tip: its centre must read over_tip, not the word beneath.
        reader.graph.tooltip.scroll_tip(round(reader.graph.screen.osd[1] * 0.3))
        for _ in range(3):
            reader.pump()
            time.sleep(0.02)
        sx, sy, sw, sh = reader.graph.tooltip.surface_state().view.rect
        word2, tip2, _ = _targets_for(reader, int(sx + sw / 2), int(sy + sh / 2))
        assert tip2 and word2 == -1, (
            f"after scroll, tooltip centre must still read over_tip; got over_tip={tip2} "
            f"over_word={word2} — _tip_rect={reader.graph.tooltip.surface_state().view.rect} (post-scroll windowed rect calc?)"
        )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_forced_mouse_section_beats_a_rival_forced_mbtn_left():
    """The real-world bug: a script (uosc/inputevent) force-binds MBTN_LEFT, so saitenka's plain
    keybind never fires. saitenka's own FORCED section, enabled while a tooltip is up, must win the
    click back. Simulate the rival with a forced 'MBTN_LEFT cycle pause' and verify a click over the
    tooltip does NOT toggle pause (saitenka intercepted it), while one over bare video still does."""

    def pause_state() -> bool:
        return bool(ipc.command("get_property", "pause").get("data"))

    with _live_reader() as (_tmp, reader, ipc):
        ipc.command("define-section", "rival", "MBTN_LEFT cycle pause\n", "force")
        ipc.command("enable-section", "rival", "allow-hide-cursor+allow-vo-dragging")

        # baseline: no tooltip → saitenka's section is off → the rival owns the click and toggles pause
        ipc.command("set_property", "pause", True)  # noqa: FBT003  # mpv IPC wire value
        reader.pump()  # nothing claims mouse capture, so the section stays disabled
        ipc.command("mouse", 5, 5)  # bare video, no word
        ipc.command("keypress", "MBTN_LEFT")
        for _ in range(5):
            reader.pump()
            time.sleep(0.02)
        assert pause_state() is False, "rival forced MBTN_LEFT should toggle pause off the tooltip"

        # hover a word → tooltip up → poll enables saitenka's forced section on top of the rival
        i = next(
            k
            for k, t in enumerate(reader.graph.subtitle_presentation.cue.current.tokens)
            if t.is_content
        )
        box = next(b for b in reader.graph.subtitle_presentation.cue.current.boxes if b.index == i)
        ox, oy = reader.graph.subtitle_presentation.cue.current.origin
        ipc.command("mouse", int(ox + box.x + box.w / 2), int(oy + box.y + box.h / 2))
        _poll_until(
            reader,
            lambda: (
                reader.graph.tooltip.surface_state().view.rect is not None
                and reader.graph.mouse.held
            ),
            "tooltip did not show / mouse section not captured",
        )

        # a click on the tooltip must reach saitenka, NOT the rival → pause unchanged
        ipc.command("set_property", "pause", True)  # noqa: FBT003  # mpv IPC wire value
        tx, ty, tw, th = reader.graph.tooltip.surface_state().view.rect
        ipc.command("mouse", int(tx + tw / 2), int(ty + th / 2))
        ipc.command("keypress", "MBTN_LEFT")
        for _ in range(5):
            reader.pump()
            time.sleep(0.02)
        assert pause_state() is True, (
            "saitenka's forced section must capture the click, not the rival"
        )


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_overlay_toggle_removes_and_restores_saitenka_surfaces():
    with _live_reader() as (tmp, reader, ipc):
        shown = _screenshot(ipc, tmp / "overlay-shown.png")

        ipc.command("keypress", "Alt+o")
        _poll_until(reader, lambda: not reader.graph.overlay.visible, "Alt+o did not hide Saitenka")
        assert ipc.command("get_property", "sub-visibility").get("data") is True
        assert ipc.command("get_property", "osd-level").get("data") == 1
        hidden = _screenshot(ipc, tmp / "overlay-hidden.png")

        ipc.command("keypress", "Alt+o")
        _poll_until(reader, lambda: reader.graph.overlay.visible, "Alt+o did not restore Saitenka")
        assert ipc.command("get_property", "sub-visibility").get("data") is False
        # osd-level stays at mpv's default (1) in both states — the overlay no longer forces it to 0
        assert ipc.command("get_property", "osd-level").get("data") == 1
        restored = _screenshot(ipc, tmp / "overlay-restored.png")

        assert ImageChops.difference(shown, hidden).getbbox() is not None
        assert ImageChops.difference(restored, hidden).getbbox() is not None


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_sidebar_key_draws_and_removes_sidebar():
    with _live_reader() as (tmp, reader, ipc):
        closed = _screenshot(ipc, tmp / "sidebar-closed.png")

        ipc.command("keypress", "\\")
        _poll_until(
            reader,
            lambda: reader.graph.sidebar.state.open,
            "sidebar key did not open the sidebar",
        )
        opened = _screenshot(ipc, tmp / "sidebar-open.png")

        ipc.command("keypress", "\\")
        _poll_until(
            reader,
            lambda: not reader.graph.sidebar.state.open,
            "sidebar key did not close the sidebar",
        )

        assert reader.graph.sidebar.panel.rect is None
        assert ImageChops.difference(opened, closed).getbbox() is not None


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_help_key_draws_and_escape_closes_shortcut_reference():
    with _live_reader() as (tmp, reader, ipc):
        closed = _screenshot(ipc, tmp / "help-closed.png")

        ipc.command("keypress", "F1")
        _poll_until(reader, lambda: reader.graph.help.state.open, "F1 did not open shortcut help")
        opened = _screenshot(ipc, tmp / "help-open.png")

        ipc.command("keypress", "ESC")
        _poll_until(
            reader, lambda: not reader.graph.help.state.open, "Esc did not close shortcut help"
        )

        assert ImageChops.difference(opened, closed).getbbox() is not None
