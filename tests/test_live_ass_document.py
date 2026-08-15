"""Opt-in differential check of Saitenka ASS decoding against mpv ``sub-text``."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from live_harness import make_clip_and_sub

from saitenka.mpvio.discover import find_mpv
from saitenka.mpvio.ipc import MpvIPC, default_ipc_path
from saitenka.subtitles import RawSubtitleEvent, SubtitleEventId, SubtitleTrackId, decode_ass_event

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAITENKA_LIVE"),
    reason="live real-mpv test — set SAITENKA_LIVE=1; run `uv run poe smoke-live`",
)

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,24,24,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@pytest.mark.live
@pytest.mark.timeout(30)
def test_decode_ass_matches_mpv_sub_text_for_supported_static_event() -> None:
    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")
    raw = r"{\an7\alpha&H40&}猫\N犬\h鳥{\b1}を見る{\b0}"
    identity = SubtitleEventId(SubtitleTrackId("live:ass:1"), 0, 8_000, 0, 0)
    expected = decode_ass_event(RawSubtitleEvent(identity, raw)).text
    workspace = Path(tempfile.mkdtemp(prefix="saitenka-ass-live-"))
    clip, _unused_srt = make_clip_and_sub(workspace)
    ass = workspace / "source.ass"
    ass.write_text(
        _HEADER + f"Dialogue: 0,0:00:00.00,0:00:08.00,Default,,0,0,0,,{raw}\n",
        encoding="utf-8",
    )
    socket = default_ipc_path(f"ass-{workspace.name[-8:]}")
    process = subprocess.Popen(
        [
            mpv,
            f"--input-ipc-server={socket}",
            "--vo=null",
            "--ao=null",
            "--keep-open=yes",
            "--pause",
            "--start=1",
            "--no-config",
            f"--sub-file={ass}",
            str(clip),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    ipc = None
    try:
        try:
            ipc = MpvIPC(socket).connect(timeout=15)
        except TimeoutError as error:
            stderr = (
                process.stderr.read().decode(errors="replace")
                if process.poll() is not None and process.stderr
                else "mpv remained alive without creating its IPC endpoint"
            )
            raise AssertionError(
                f"mpv did not expose IPC (returncode={process.returncode}): {stderr}"
            ) from error
        deadline = time.monotonic() + 5
        observed = ""
        while time.monotonic() < deadline and not observed:
            observed = str(ipc.command("get_property", "sub-text").get("data") or "")
            time.sleep(0.02)
        assert observed == expected
    finally:
        if ipc is not None:
            try:
                ipc.command("quit")
                ipc.close()
            except Exception:  # noqa: BLE001
                pass
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
