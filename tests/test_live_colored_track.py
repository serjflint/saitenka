"""The colored copy's colors are in mpv's own subtitle render, from the first frame of a cue.

`screenshot-to-file … subtitles` renders the subtitle track and leaves the OSD out, so a Saitenka
overlay cannot pass this: whatever color is in the capture came from the track mpv is playing.
"""

from __future__ import annotations

import os
import time

import numpy as np
import pytest
from live_harness import LayoutLiveOptions, live_reader, poll_until
from PIL import Image

from saitenka.app import colored_subs
from saitenka.app.subtitle_render import whole_cue_device
from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(30),
    pytest.mark.mpv_min(NATIVE_GEOMETRY_MPV_MIN),
    pytest.mark.skipif(
        not os.environ.get("SAITENKA_LIVE"), reason="requires live font/display environment"
    ),
]

CUES = ((0.5, 3.0, "猫を見る"), (4.0, 6.5, "犬も見る"))


def _selected(ipc) -> dict:
    return next(
        track
        for track in ipc.command("get_property", "track-list")["data"]
        if track.get("type") == "sub" and track.get("selected")
    )


def _colored_pixels(path, boxes, rgb) -> int:
    image = np.asarray(Image.open(path).convert("RGB")).astype(int)
    target = np.array(rgb)
    count = 0
    for box in boxes:
        window = image[box.y : box.y + box.h, box.x : box.x + box.w]
        count += int((np.abs(window - target).max(axis=-1) <= 24).sum())
    return count


def _swap_in_a_gap(reader, ipc) -> None:
    """The copy is swapped in between cues, never under a paused line."""
    ipc.command("seek", "3.5", "absolute+exact")
    poll_until(
        reader,
        lambda: colored_subs.is_copy(_selected(ipc)),
        "the colored copy was never selected",
    )
    ipc.command("seek", "0.6", "absolute+exact")


def _capture(ipc, tmp, name: str) -> object:
    path = tmp / f"{name}.png"
    reply = ipc.command("screenshot-to-file", str(path), "subtitles")
    assert reply.get("error") == "success", reply
    return path


def test_the_copy_colors_the_cue_in_mpvs_own_subtitle_render() -> None:
    from test_cue_color_timeline import _coloring

    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        cues=CUES,
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            ("--sub-ass-override=no", "--blend-subtitles=no"),
            start_seconds=0.6,
            prefetch=False,
        ),
    ) as (tmp, reader, ipc):
        _swap_in_a_gap(reader, ipc)
        poll_until(
            reader,
            lambda: bool(reader.graph.subtitle_presentation.cue.current.boxes),
            "the copy's cue never got hit boxes",
        )
        assert whole_cue_device(reader.graph.cue.draw_request()) == ("track", "track")
        cue = reader.graph.subtitle_presentation.cue.current
        unknown = next(
            (box, style.color[:3])
            for box, style in ((box, cue.styles[box.index]) for box in cue.boxes)
            if tuple(style.color[:3]) != (255, 255, 255) and box.index != 0
        )
        colored = _capture(ipc, tmp, "colored")

        original = next(
            track
            for track in ipc.command("get_property", "track-list")["data"]
            if track.get("type") == "sub" and not colored_subs.is_copy(track)
        )
        ipc.command("set_property", "sid", original["id"])
        time.sleep(0.3)
        plain = _capture(ipc, tmp, "plain")

        box, rgb = unknown
        painted, authored = _colored_pixels(colored, [box], rgb), _colored_pixels(plain, [box], rgb)
        # Antialiased white-on-navy edges pass near a pale token color, so the original scores a
        # few pixels; the copy fills the glyph interiors.
        assert painted > 10 * max(authored, 5), (painted, authored)


def test_the_next_cue_is_colored_on_the_frame_a_sub_seek_lands_on() -> None:
    from test_cue_color_timeline import _coloring

    with live_reader(
        native_visible=True,
        scorer=_coloring(),
        cues=CUES,
        layout=LayoutLiveOptions(
            "shadow",
            os.environ.get("SAITENKA_LAYOUT_MPV"),
            ("--sub-ass-override=no", "--blend-subtitles=no"),
            start_seconds=0.6,
            prefetch=False,
        ),
    ) as (tmp, reader, ipc):
        _swap_in_a_gap(reader, ipc)
        ipc.command("sub-seek", "1")
        # No pump: whatever Saitenka would do in reaction to the new cue has not run yet.
        time.sleep(0.2)
        landed = _capture(ipc, tmp, "landed")

        poll_until(
            reader,
            lambda: bool(reader.graph.subtitle_presentation.cue.current.boxes),
            "the landed cue never got hit boxes",
        )
        cue = reader.graph.subtitle_presentation.cue.current
        boxes = [box for box in cue.boxes if tuple(cue.styles[box.index].color[:3]) != (255,) * 3]
        rgb = cue.styles[boxes[0].index].color[:3]
        origin = next(
            track
            for track in ipc.command("get_property", "track-list")["data"]
            if track.get("type") == "sub" and not colored_subs.is_copy(track)
        )
        ipc.command("set_property", "sid", origin["id"])
        ipc.command("seek", "0.6", "absolute+exact")
        time.sleep(0.2)
        ipc.command("sub-seek", "1")
        time.sleep(0.2)
        plain = _capture(ipc, tmp, "landed-plain")

        painted, authored = _colored_pixels(landed, boxes, rgb), _colored_pixels(plain, boxes, rgb)
        assert painted > 10 * max(authored, 5), (painted, authored)
