"""Opt-in differential check of Saitenka ASS decoding against mpv ``sub-text``."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
from live_harness import make_clip_and_sub
from saitenka_subtitles import RawSubtitleEvent, SubtitleEventId, SubtitleTrackId, decode_ass_event

from saitenka.mpvio.discover import find_mpv
from saitenka.mpvio.ipc import MpvIPC, default_ipc_path

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


def _observe_mpv_subtitle_properties(
    mpv: str,
    event_rows: str,
    properties: tuple[str, ...],
    *,
    start: float = 1.0,
    sub_delay: float = 0.0,
) -> dict[str, dict]:
    workspace = Path(tempfile.mkdtemp(prefix="saitenka-ass-live-"))
    clip, _unused_srt = make_clip_and_sub(workspace)
    ass = workspace / "source.ass"
    ass.write_text(_HEADER + event_rows, encoding="utf-8")
    socket = default_ipc_path(f"ass-{workspace.name[-8:]}")
    process = subprocess.Popen(
        [
            mpv,
            f"--input-ipc-server={socket}",
            "--vo=null",
            "--ao=null",
            "--keep-open=yes",
            "--pause",
            f"--start={start}",
            f"--sub-delay={sub_delay}",
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
        while time.monotonic() < deadline:
            if ipc.command("get_property", "sub-text").get("data"):
                return {name: ipc.command("get_property", name) for name in properties}
            time.sleep(0.02)
        raise AssertionError("mpv did not activate the authored ASS event")
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


@pytest.mark.live
@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "raw",
    [
        r"{\an7\alpha&H40&}猫\N犬\h鳥{\b1}を見る{\b0}",
        r"前{\p1}m 0 0 l 10 10{\p0}中{\pos(20,30)}後",
        r"猫\n犬",
    ],
)
def test_decode_ass_matches_mpv_sub_text(raw: str) -> None:
    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")
    identity = SubtitleEventId(SubtitleTrackId("live:ass:1"), 0, 8_000, 0, 0)
    expected = decode_ass_event(RawSubtitleEvent(identity, raw)).text
    replies = _observe_mpv_subtitle_properties(
        mpv,
        f"Dialogue: 0,0:00:00.00,0:00:08.00,Default,,0,0,0,,{raw}\n",
        ("sub-text",),
    )
    assert replies["sub-text"].get("error") == "success"
    assert replies["sub-text"].get("data") == expected


@pytest.mark.live
@pytest.mark.mpv_min("0.38")  # sub-text/ass-full
@pytest.mark.timeout(30)
def test_mpv_ass_full_preserves_simultaneous_event_order_and_metadata() -> None:
    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")
    rows = (
        "Dialogue: 0,0:00:00.00,0:00:08.00,Default,dialogue,0,0,0,,猫\n"
        "Dialogue: 1,0:00:00.50,0:00:07.00,Default,sign,12,34,56,,犬\n"
    )

    replies = _observe_mpv_subtitle_properties(mpv, rows, ("sub-text", "sub-text/ass-full"))

    assert replies["sub-text"].get("error") == "success"
    assert replies["sub-text"].get("data") == "猫\n犬"
    assert replies["sub-text/ass-full"].get("error") == "success"
    assert replies["sub-text/ass-full"].get("data") == (
        "Dialogue: 0,0:00:00.00,0:00:08.00,Default,dialogue,0000,0000,0000,,猫\n"
        "Dialogue: 1,0:00:00.50,0:00:07.00,Default,sign,0012,0034,0056,,犬"
    )


@pytest.mark.parametrize(("start", "sub_delay"), [(1.5, 0.5), (0.5, -0.5)])
@pytest.mark.live
@pytest.mark.mpv_min("0.38")  # sub-text/ass-full
@pytest.mark.timeout(30)
def test_mpv_selects_active_ass_rows_on_the_delay_adjusted_subtitle_clock(
    start: float, sub_delay: float
) -> None:
    mpv = find_mpv(None)
    if not mpv:
        pytest.skip("mpv not found")
    replies = _observe_mpv_subtitle_properties(
        mpv,
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫\n",
        ("time-pos", "sub-delay", "sub-text/ass-full"),
        start=start,
        sub_delay=sub_delay,
    )

    video_time = float(replies["time-pos"]["data"])
    observed_delay = float(replies["sub-delay"]["data"])
    assert 1.0 <= video_time - observed_delay < 3.0
    assert replies["sub-text/ass-full"]["data"].endswith("猫")
