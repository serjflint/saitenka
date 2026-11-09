"""A real mpv cursor can scan inside a subtitle token without changing that token."""

import os

import pytest
from live_harness import LayoutLiveOptions, live_reader, make_clip_and_sub, poll_until
from test_cue_color_timeline import _coloring
from test_live_colored_track import _swap_in_a_gap

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(30),
    pytest.mark.skipif(not os.environ.get("SAITENKA_LIVE"), reason="needs real mpv/display"),
]


@pytest.mark.parametrize("renderer", ["pillow", "ass", "srt"])
def test_real_cursor_scans_the_component_inside_yaritogetai(tmp_path, renderer):
    cues = ((0.5, 3.0, "やり遂げたい"), (4.0, 6.5, "猫を見る"))
    source = {}
    if renderer == "srt":
        clip, subtitles = make_clip_and_sub(tmp_path, cues)
        source = {"media": clip, "subtitles": subtitles}
    with live_reader(
        native_visible=renderer != "pillow",
        scorer=_coloring(),
        cues=cues,
        layout=LayoutLiveOptions(
            start_seconds=0.6,
            prefetch=False,
            extra_args=("--geometry=1280x800", "--hidpi-window-scale=no", "--blend-subtitles=no"),
            **source,
        ),
    ) as (_tmp, reader, ipc):
        if renderer != "pillow":
            _swap_in_a_gap(reader, ipc)
        reader.graph.tooltip.configure_delays(scan=0.01, switch=0.0)
        poll_until(
            reader,
            lambda: any(
                box.characters for box in reader.graph.subtitle_presentation.cue.current.boxes
            ),
            "subtitle never acquired qualified character cells",
        )

        def aim(offset):
            cue = reader.graph.subtitle_presentation.cue.current
            cell = next(
                char for box in cue.boxes for char in box.characters if char.offset == offset
            )
            rect = cell.bounds
            ipc.command(
                "mouse",
                round(cue.origin[0] + rect.x + rect.width / 2),
                round(cue.origin[1] + rect.y + rect.height / 2),
            )

        aim(0)
        poll_until(
            reader,
            lambda: reader.graph.tooltip.surface_state().view.state is not None,
            "whole-word tooltip did not open",
        )
        aim(2)
        poll_until(
            reader,
            lambda: reader.graph.tooltip.surface_state().nest.state is not None,
            "component tooltip did not open",
        )
        from saitenka.app.overlay_ids import OverlayId
        from saitenka.runtime import SurfaceStatus

        poll_until(
            reader,
            lambda: (
                reader.graph.interaction_surfaces.snapshot(OverlayId.TIP).status
                is SurfaceStatus.ABSENT
            ),
            "previous whole-word tooltip remained visible",
        )
        token = reader.graph.tooltip.surface_state().nest.token
        assert (token.surface, token.lemma) == ("遂げたい", "遂げる")
        assert [
            token.surface for token in reader.graph.subtitle_presentation.cue.current.tokens
        ] == ["やり遂げたい"]
