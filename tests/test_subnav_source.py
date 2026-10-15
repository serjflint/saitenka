"""Replacing the subtitle source must not blank the cue that is still on screen.

`load_sub_index` retires the cue identity — a new authored source means the old identity is gone.
Under the tick that cost nothing: the next poll re-read `sub-text` and rebuilt it. Cue arrival is
event-driven now, so mpv sends nothing until its *next* change, and a mid-session track switch left
the overlay blank for the rest of the cue. Only `poe smoke-live` saw it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from util import FakeIPC, keybind_registry

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import NullRenderer

if TYPE_CHECKING:
    from session_builder import TestSession

CUE = "門前の小僧習わぬ経を読む"
NEXT_CUE = "次の字幕"


def _reader_showing_the_cue(make_session) -> TestSession:
    """A session with the cue on screen, established the way mpv establishes it."""
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    reader = make_session(
        ipc,
        infrastructure=SessionInfrastructure(
            renderer=NullRenderer(),
        ),
        options=ReaderOptions().with_overrides(
            prefetch=False,
        ),
    )
    reader.graph.commands.install_input()
    reader.graph.presentation.refresh_osd()
    ipc.props["sub-text"] = CUE
    reader.graph.playback.observe("sub-text", CUE)
    reader.pump()
    assert reader.graph.subtitle_presentation.cue.current.tokens, (
        "the observed cue should be tokenized before the source changes"
    )
    return reader


def _srt(tmp_path, name: str = "line.srt"):
    path = tmp_path / name
    path.write_text(
        f"1\n00:00:00,000 --> 00:00:08,000\n{CUE}\n\n"
        f"2\n00:00:09,000 --> 00:00:12,000\n{NEXT_CUE}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_loading_a_subtitle_index_keeps_the_cue_already_on_screen(tmp_path, make_session) -> None:
    reader = _reader_showing_the_cue(make_session)

    reader.graph.subtitle_navigation.load_index(_srt(tmp_path))

    assert reader.graph.subtitle_presentation.cue.current.tokens
    assert reader.graph.playback.cue.text == CUE
    ipc = reader.graph.ipc
    reader.command(keybind_registry(ipc)["Alt+RIGHT"])
    assert reader.graph.playback.cue.text == NEXT_CUE
    assert ("seek", "9.01", "absolute+exact") in ipc.commands


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("failure", ["empty", "malformed", "missing"])
def test_an_unreadable_index_leaves_the_cue_untouched(tmp_path, make_session, failure) -> None:
    reader = _reader_showing_the_cue(make_session)
    reader.graph.subtitle_navigation.load_index(_srt(tmp_path))
    before = [token.surface for token in reader.graph.subtitle_presentation.cue.current.tokens]
    failed = tmp_path / f"{failure}.srt"
    if failure != "missing":
        failed.write_text("" if failure == "empty" else "not a subtitle file\n", encoding="utf-8")

    reader.graph.subtitle_navigation.load_index(failed)

    assert [
        token.surface for token in reader.graph.subtitle_presentation.cue.current.tokens
    ] == before
    ipc = reader.graph.ipc
    reader.command(keybind_registry(ipc)["Alt+RIGHT"])
    assert reader.graph.playback.cue.text == NEXT_CUE
    assert ("seek", "9.01", "absolute+exact") in ipc.commands
