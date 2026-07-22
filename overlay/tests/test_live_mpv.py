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
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1 (needs a display); run `uv run poe smoke-live`",
)

DEMO_LINE = "門前の小僧習わぬ経を読む"


class _MiniDS:
    """A trivial dict so a tooltip renders — L3 is about the input path / alignment, not content."""

    def entry_for(self, tok, inflected=None):
        from overlay.panel import Definition, Entry

        return Entry(
            headword=[tok.surface],
            reading=getattr(tok, "reading", "") or tok.surface,
            defs=[Definition("D", ["to read"])],
        )

    def has_term(self, *forms):
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


def test_live_real_mouse_shows_tooltip_on_the_aimed_word():
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

        for _ in range(100):  # wait for the subtitle cue → tokens + per-word boxes
            reader.poll_once()
            if reader.tokens and reader.boxes:
                break
            time.sleep(0.1)
        assert reader.tokens and reader.boxes, "subtitle never loaded into the reader"

        # aim a REAL mouse move at the screen centre of a content word
        i = next(k for k, t in enumerate(reader.tokens) if t.is_content)
        box = next(b for b in reader.boxes if b.index == i)
        ox, oy = reader.sub_origin
        cx, cy = int(ox + box.x + box.w / 2), int(oy + box.y + box.h / 2)
        ipc.command("mouse", cx, cy)

        for _ in range(60):  # let mpv emit mouse-pos → poll → _update_hover → tooltip
            reader.poll_once()
            if reader._tip_rect is not None:
                break
            time.sleep(0.05)
        ipc.command("screenshot-to-file", str(tmp / "live_hover.png"), "window")

        assert reader._tip_rect is not None, "a real mouse over a word did not show a tooltip"
        # R1: the hovered word must be the one we aimed at — this is the mouse-pos→OSD alignment the
        # headless tests can't check. A mismatch here is the HiDPI scaling bug.
        assert reader.hover == i, (
            f"hover misaligned: aimed word {i} ({reader.tokens[i].surface!r}), "
            f"got {reader.hover} — mouse-pos→OSD mapping (HiDPI/R1)? screenshot: {tmp / 'live_hover.png'}"
        )

        # a real keypress must reach the reader (mine key is bound) — drive it and drain
        reader.poll_once()
    finally:
        try:
            if reader is not None:
                reader.close()
            if ipc is not None:
                ipc.command("quit")
                ipc.close()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
