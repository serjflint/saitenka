"""#251: the card preview's ▶ clip is a fire-and-forget player that outlived the panel — every dismiss
path (✕ / Esc / new-cue / P) only hid the overlay, so the audio kept playing. These pin that the
player handle is retained and stopped on every path.

The real kill is procutil's job (tested there); here ``kill_process_tree`` is a recorder, so the fake
handle never needs a real pid — we assert the *wiring* routes the handle to the killer and clears it."""

from __future__ import annotations

import pytest
from util import FakeIPC

from saitenka.app import miner_ui
from saitenka.app.card_preview import PreviewData
from saitenka.app.controller import Reader


class _FakeProc:
    """Stands in for the ▶ clip's Popen; kill_process_tree is patched to a recorder, so no real pid."""


def _preview_data() -> PreviewData:
    return PreviewData(
        status="mined", expression="読む", reading="よむ", sentence_lines=["本を読む"]
    )


@pytest.fixture
def reader_with_clip(tmp_path, monkeypatch):
    """A reader whose ▶ clip is 'playing': the ▶ branch stored an injected handle, and every stop is
    recorded instead of touching a real process."""
    killed: list = []
    monkeypatch.setattr(miner_ui, "kill_process_tree", killed.append)
    proc = _FakeProc()
    monkeypatch.setattr(miner_ui, "play_audio", lambda _p: proc)
    clip = tmp_path / "clip.opus"
    clip.write_bytes(b"x")
    r = Reader(FakeIPC())
    r.preview.last_preview = _preview_data()
    r.preview.last_audio = clip
    r.preview.rect = (0, 0, 200, 200)
    r.preview.audio_rect = (10, 10, 40, 40)
    r.preview.close_rect = (60, 10, 20, 20)
    miner_ui.click_preview(r, 20, 20)  # ▶ → retains the handle
    assert r.preview.audio_proc is proc  # precondition: a clip is 'playing'
    return r, proc, killed


def test_play_button_retains_the_player_handle(reader_with_clip):
    r, proc, _killed = reader_with_clip
    assert r.preview.audio_proc is proc  # the fire-and-forget Popen is now stoppable


def test_second_play_press_replaces_the_clip_never_stacks(reader_with_clip, monkeypatch):
    r, first, killed = reader_with_clip
    second = _FakeProc()
    monkeypatch.setattr(miner_ui, "play_audio", lambda _p: second)
    miner_ui.click_preview(r, 20, 20)  # ▶ again while the first still plays
    assert first in killed and r.preview.audio_proc is second


def _close_button(r: Reader) -> None:
    miner_ui.click_preview(r, 65, 15)  # ✕ → hide_preview


def _esc(r: Reader) -> None:
    r._hide_preview()  # Esc → PREVIEW_CLOSE_MSG → _hide_preview


def _new_cue(r: Reader) -> None:
    r.set_subtitle("次のセリフ")  # a cue change auto-dismisses the last preview


def _replay(r: Reader) -> None:
    r.replay_preview()  # P → re-show, which silences the current clip


@pytest.mark.parametrize(
    "dismiss", [_close_button, _esc, _new_cue, _replay], ids=["close", "esc", "new_cue", "replay"]
)
def test_every_dismiss_path_stops_the_clip(reader_with_clip, dismiss):
    r, proc, killed = reader_with_clip
    dismiss(r)
    assert proc in killed  # the clip was stopped, not left playing (#251)
    assert r.preview.audio_proc is None  # handle cleared → nothing lingers
