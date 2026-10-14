"""#251: the card preview's ▶ clip is a fire-and-forget player that outlived the panel — every dismiss
path (✕ / Esc / new-cue / P) only hid the overlay, so the audio kept playing. These pin that the
player handle is retained and stopped on every path.

The real kill is procutil's job (tested there); here ``kill_process_tree`` is a recorder, so the fake
handle never needs a real pid — we assert the *wiring* routes the handle to the killer and clears it."""

from __future__ import annotations

import pytest
from session_builder import TestSession, build_session
from util import FakeIPC

from saitenka.app.features.preview import miner_ui
from saitenka.app.features.preview.card_preview import PreviewData
from saitenka.runtime import events


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_dismissal_kills_a_player_that_ignores_graceful_termination(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    if os.name == "nt":
        pytest.skip("Windows terminate is unconditional")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
    )
    try:
        assert proc.stdout.readline() == b"ready\n"
        monkeypatch.setattr(miner_ui, "play_audio", lambda _path: proc)
        clip = tmp_path / "clip.opus"
        clip.write_bytes(b"x")
        reader = build_session(FakeIPC())
        reader.graph.preview.store.dispatch(events.PreviewShown(_preview_data(), clip))
        panel = reader.graph.preview.panel
        panel.rect, panel.audio_rect = (0, 0, 200, 200), (10, 10, 40, 40)
        miner_ui.click_preview(reader.graph.preview_commands.ports(), 20, 20)

        reader.graph.preview_commands.hide()

        assert proc.wait(timeout=1) != 0
        assert reader.graph.preview.panel.audio_proc is None
    finally:
        proc.kill()
        proc.wait(timeout=1)
        proc.stdout.close()


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

    def kill(proc, *, timeout=5):
        assert timeout == 0
        killed.append(proc)

    monkeypatch.setattr(miner_ui, "kill_process_tree", kill)
    proc = _FakeProc()
    monkeypatch.setattr(miner_ui, "play_audio", lambda _p: proc)
    clip = tmp_path / "clip.opus"
    clip.write_bytes(b"x")
    r = build_session(FakeIPC())
    r.graph.preview.store.dispatch(events.PreviewShown(_preview_data(), clip))
    panel = r.graph.preview.panel
    panel.rect = (0, 0, 200, 200)
    panel.audio_rect = (10, 10, 40, 40)
    panel.close_rect = (60, 10, 20, 20)
    miner_ui.click_preview(r.graph.preview_commands.ports(), 20, 20)  # ▶ → retains the handle
    assert panel.audio_proc is proc  # precondition: a clip is 'playing'
    return r, proc, killed


def test_second_play_press_replaces_the_clip_never_stacks(reader_with_clip, monkeypatch):
    r, first, killed = reader_with_clip
    second = _FakeProc()
    monkeypatch.setattr(miner_ui, "play_audio", lambda _p: second)
    miner_ui.click_preview(
        r.graph.preview_commands.ports(), 20, 20
    )  # ▶ again while the first still plays
    assert first in killed and r.graph.preview.panel.audio_proc is second


def _close_button(r: TestSession) -> None:
    miner_ui.click_preview(r.graph.preview_commands.ports(), 65, 15)  # ✕ → hide_preview


def _esc(r: TestSession) -> None:
    r.graph.preview_commands.hide()


def _new_cue(r: TestSession) -> None:
    r.graph.cue.set_subtitle("次のセリフ")  # a cue change auto-dismisses the last preview


def _replay(r: TestSession) -> None:
    r.graph.preview_commands.replay()  # P → re-show, which silences the current clip


@pytest.mark.parametrize(
    "dismiss", [_close_button, _esc, _new_cue, _replay], ids=["close", "esc", "new_cue", "replay"]
)
def test_every_dismiss_path_stops_the_clip(reader_with_clip, dismiss):
    r, proc, killed = reader_with_clip
    dismiss(r)
    assert proc in killed  # the clip was stopped, not left playing (#251)
    assert r.graph.preview.panel.audio_proc is None  # handle cleared → nothing lingers
