"""L3 real-mpv smoke — inject REAL mouse/key events into a LIVE mpv and verify the overlay reacts.

Opt-in (needs a real display): ``SAITENKA_LIVE=1`` — `uv run poe smoke-live`. Skipped in the normal
gate. This is the only layer that exercises mpv's ``mouse-pos`` → OSD coordinate mapping: the
HiDPI/Retina hit-alignment (R1) the headless FakeIPC tests structurally can't reach, because they set
``mouse-pos`` directly in OSD coords. It drives mpv's own ``mouse`` / ``keypress`` input commands and
saves a screenshot artifact.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image, ImageChops

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1 (needs a display); run `uv run poe smoke-live`",
)

DEMO_LINE = "門前の小僧習わぬ経を読む"


class _MiniDS:
    """A trivial dict so a tooltip renders — L3 is about the input path / alignment, not content."""

    def entry_for(self, tok, _inflected=None):
        from overlay.panel import Definition, Entry

        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition("D", ["to read"])],
        )

    def has_term(self, *_forms):
        return True


def _make_clip_and_sub(tmp: Path) -> tuple[Path, Path]:
    clip = tmp / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=navy:s=1280x720:d=8",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    srt = tmp / "line.srt"
    srt.write_text(f"1\n00:00:00,000 --> 00:00:08,000\n{DEMO_LINE}\n", encoding="utf-8")
    return clip, srt


@contextmanager
def _live_reader():
    from overlay.app.controller import Reader
    from overlay.mpvio.discover import find_mpv
    from overlay.mpvio.ipc import MpvIPC, default_ipc_path

    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")

    tmp = Path(tempfile.mkdtemp(prefix="saitenka-live-"))
    clip, srt = _make_clip_and_sub(tmp)
    sock = default_ipc_path(tmp.name)
    proc = subprocess.Popen(
        [
            mpv,
            f"--input-ipc-server={sock}",
            "--force-window=yes",
            "--keep-open=yes",
            "--sub-visibility=no",
            "--osd-level=0",
            "--pause",
            "--no-config",
            f"--sub-file={srt}",
            str(clip),
        ]
    )
    reader = ipc = None
    try:
        ipc = MpvIPC(sock).connect(timeout=15)
        reader = Reader(ipc, dict_set=_MiniDS())
        reader.refresh_osd()
        reader.start_observing()
        reader._register_keybinds()
        reader.load_sub_index(srt)

        for _ in range(100):  # wait for the subtitle cue → tokens + per-word boxes
            reader.poll_once()
            if reader.tokens and reader.boxes:
                break
            time.sleep(0.1)
        assert reader.tokens and reader.boxes, "subtitle never loaded into the reader"
        yield tmp, reader, ipc
    finally:
        try:
            if reader is not None:
                reader.close()
            if ipc is not None:
                ipc.command("quit")
                ipc.close()
        except Exception:  # noqa: BLE001  # best-effort teardown - preserve the test's assertion
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _poll_until(reader, predicate, message: str) -> None:
    for _ in range(60):
        reader.poll_once()
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(message)


def _screenshot(ipc, path: Path) -> Image.Image:
    response = ipc.command("screenshot-to-file", str(path), "window")
    assert response.get("error") == "success"
    return Image.open(path).convert("RGB")


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_real_mouse_shows_tooltip_on_the_aimed_word():
    with _live_reader() as (tmp, reader, ipc):
        # aim a REAL mouse move at the screen centre of a content word
        i = next(k for k, t in enumerate(reader.tokens) if t.is_content)
        box = next(b for b in reader.boxes if b.index == i)
        ox, oy = reader.sub_origin
        cx, cy = int(ox + box.x + box.w / 2), int(oy + box.y + box.h / 2)
        ipc.command("mouse", cx, cy)

        _poll_until(
            reader,
            lambda: reader._tip_rect is not None,
            "a real mouse over a word did not show a tooltip",
        )
        ipc.command("screenshot-to-file", str(tmp / "live_hover.png"), "window")

        # R1: the hovered word must be the one we aimed at — this is the mouse-pos→OSD alignment the
        # headless tests can't check. A mismatch here is the HiDPI scaling bug.
        assert reader.hover == i, (
            f"hover misaligned: aimed word {i} ({reader.tokens[i].surface!r}), "
            f"got {reader.hover} — mouse-pos→OSD mapping (HiDPI/R1)? screenshot: {tmp / 'live_hover.png'}"
        )

        # a real keypress must reach the reader (mine key is bound) — drive it and drain
        reader.poll_once()


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_overlay_toggle_removes_and_restores_saitenka_surfaces():
    with _live_reader() as (tmp, reader, ipc):
        shown = _screenshot(ipc, tmp / "overlay-shown.png")

        ipc.command("keypress", "Alt+o")
        _poll_until(reader, lambda: not reader.ov.visible, "Alt+o did not hide Saitenka")
        assert ipc.command("get_property", "sub-visibility").get("data") is True
        assert ipc.command("get_property", "osd-level").get("data") == 1
        hidden = _screenshot(ipc, tmp / "overlay-hidden.png")

        ipc.command("keypress", "Alt+o")
        _poll_until(reader, lambda: reader.ov.visible, "Alt+o did not restore Saitenka")
        assert ipc.command("get_property", "sub-visibility").get("data") is False
        assert ipc.command("get_property", "osd-level").get("data") == 0
        restored = _screenshot(ipc, tmp / "overlay-restored.png")

        assert ImageChops.difference(shown, hidden).getbbox() is not None
        assert ImageChops.difference(restored, hidden).getbbox() is not None


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_sidebar_key_draws_and_removes_sidebar():
    with _live_reader() as (tmp, reader, ipc):
        closed = _screenshot(ipc, tmp / "sidebar-closed.png")

        ipc.command("keypress", "\\")
        _poll_until(reader, lambda: reader.sidebar.open, "sidebar key did not open the sidebar")
        opened = _screenshot(ipc, tmp / "sidebar-open.png")

        ipc.command("keypress", "\\")
        _poll_until(
            reader, lambda: not reader.sidebar.open, "sidebar key did not close the sidebar"
        )

        assert reader.sidebar.rect is None
        assert ImageChops.difference(opened, closed).getbbox() is not None


@pytest.mark.live
@pytest.mark.timeout(30)
def test_live_help_key_draws_and_escape_closes_shortcut_reference():
    with _live_reader() as (tmp, reader, ipc):
        closed = _screenshot(ipc, tmp / "help-closed.png")

        ipc.command("keypress", "F1")
        _poll_until(reader, lambda: reader._help_open, "F1 did not open shortcut help")
        opened = _screenshot(ipc, tmp / "help-open.png")

        ipc.command("keypress", "ESC")
        _poll_until(reader, lambda: not reader._help_open, "Esc did not close shortcut help")

        assert ImageChops.difference(opened, closed).getbbox() is not None
