"""Replacing the subtitle source must not blank the cue that is still on screen.

`load_sub_index` retires the cue identity — a new authored source means the old identity is gone.
Under the tick that cost nothing: the next poll re-read `sub-text` and rebuilt it. Cue arrival is
event-driven now, so mpv sends nothing until its *next* change, and a mid-session track switch left
the overlay blank for the rest of the cue. Only `poe smoke-live` saw it.
"""

from __future__ import annotations

from util import FakeIPC

from saitenka.app.session_controller import SessionController
from saitenka.app.subtitle_render import NullRenderer

CUE = "門前の小僧習わぬ経を読む"


def _reader_showing_the_cue() -> SessionController:
    """A session with the cue on screen, established the way mpv establishes it."""
    ipc = FakeIPC()
    ipc.props["osd-dimensions"] = {"w": 1280, "h": 720}
    reader = SessionController(ipc, prefetch=False, renderer=NullRenderer())
    reader.refresh_osd()
    ipc.props["sub-text"] = CUE
    reader._observe_property("sub-text", CUE)
    reader.pump()
    assert reader.tokens, "the observed cue should be tokenized before the source changes"
    return reader


def _srt(tmp_path, name: str = "line.srt"):
    path = tmp_path / name
    path.write_text(f"1\n00:00:00,000 --> 00:00:08,000\n{CUE}\n", encoding="utf-8")
    return path


def test_loading_a_subtitle_index_keeps_the_cue_already_on_screen(tmp_path) -> None:
    reader = _reader_showing_the_cue()

    reader.load_sub_index(_srt(tmp_path))

    assert [token.surface for token in reader.tokens] != []
    assert reader.sub_text == CUE
    reader.close()


def test_an_unreadable_index_leaves_the_cue_untouched(tmp_path) -> None:
    """The negative control: a fail-soft load returns before replacing the source, so the cue is
    never retired and the reinstall is not what kept it."""
    reader = _reader_showing_the_cue()
    before = [token.surface for token in reader.tokens]

    empty = tmp_path / "empty.srt"
    empty.write_text("", encoding="utf-8")
    reader.load_sub_index(empty)

    assert [token.surface for token in reader.tokens] == before
    reader.close()
