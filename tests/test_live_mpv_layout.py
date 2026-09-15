"""Real source-built mpv/libass snapshots through Saitenka's production session."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest
from dirty_equals import IsPartialDict
from live_harness import LayoutLiveOptions, live_reader, poll_until
from util import record_spans

from saitenka.app.subtitle_render import color_ladder

pytestmark = [
    pytest.mark.live,
    pytest.mark.timeout(30),
    pytest.mark.skipif(
        not (os.environ.get("SAITENKA_LIVE") and os.environ.get("SAITENKA_LAYOUT_MPV")),
        reason="requires the paired layout build in SAITENKA_LAYOUT_MPV and SAITENKA_LIVE=1",
    ),
]


@pytest.mark.parametrize(
    ("prefix", "text"),
    [
        ("", "猫を見る"),
        (r"{\kf100}", "猫を見る"),
        (r"{\alpha&H80&}", "猫を見る"),
        ("", r"猫を見る\N犬を見る"),
    ],
)
def test_auto_keeps_ordinary_shadow_paint_and_effects_scan_only(prefix, text, monkeypatch):
    spans = record_spans(monkeypatch)
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    with live_reader(
        native_visible=True,
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])), Palette()),
        cues=((0.0, 8.0, prefix + text),),
        layout=LayoutLiveOptions(
            "auto", os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_tmp, session, _ipc):
        presentation = session.graph.subtitle_presentation
        if not prefix:
            poll_until(
                session,
                lambda: presentation.cue.current.paint_allowed,
                "ordinary native geometry never admitted shadow paint",
            )
        request = session.graph.cue.draw_request()
        records = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_source"]
        assert records[-1] == IsPartialDict(
            configured_source="auto",
            selected_source="mpv",
            scan_source="mpv",
            paint_source="none" if prefix else "shadow",
            paint_allowed=not bool(prefix),
        )
        assert request.boxes[0].hit_regions
        if prefix:
            assert not request.paint_allowed
            assert not color_ladder(request).devices
        else:
            actual = color_ladder(request)
            assert any(device != "none" for device in actual.devices)
            baseline = replace(request, boxes=request.paint_boxes, paint_boxes=None)
            assert actual == color_ladder(baseline)


@pytest.mark.parametrize("prefix", ["", r"{\kf100}", r"{\alpha&H80&}"])
def test_native_layout_real_mouse_opens_the_clicked_word_without_subtitle_paint(prefix):
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, prefix + "猫を見る"),),
        layout=LayoutLiveOptions(
            "mpv", os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_tmp, session, ipc):
        presentation = session.graph.subtitle_presentation
        assert presentation.native is None
        assert presentation.cue.current.paint_allowed is False
        box = presentation.cue.current.boxes[0]
        assert box.hit_regions
        region = box.hit_regions[0]

        ipc.command("mouse", region.x + region.width // 2, region.y + region.height // 2)
        poll_until(
            session,
            lambda: session.graph.tooltip.surface_state().view.rect is not None,
            "native geometry did not open the tooltip",
        )

        assert session.graph.tooltip.observation().selected == box.index
        assert presentation.cue.current.tokens[box.index].surface == "猫"
        assert ipc.query("sub-visibility") is True

        from saitenka.app.features.mining.mining_encounter import MiningEncounterSource

        encounter = MiningEncounterSource(
            ipc,
            presentation.cue,
            session.graph.tooltip,
            session.graph.profile.profile,
            session.graph.playback,
            12,
        ).capture()
        assert encounter.cue.tokens[encounter.cue.hover].surface == "猫"
        assert encounter.span is not None
        assert (encounter.span.start, encounter.span.end) == (0.0, 8.0)
        assert encounter.media_path == str(_tmp / "clip.mp4")


def test_native_layout_rebinds_identical_text_after_seek_and_restores_collection_on_toggle():
    with live_reader(
        native_visible=True,
        cues=((0.0, 3.0, "猫を見る"), (4.0, 8.0, "猫を見る")),
        layout=LayoutLiveOptions(
            "mpv", os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_tmp, session, ipc):
        presentation = session.graph.subtitle_presentation
        first = presentation.layout.current
        ipc.command("seek", 4.1, "absolute+exact")
        poll_until(
            session,
            lambda: (
                presentation.layout.current is not None and presentation.layout.current.start == 4.0
            ),
            "repeated subtitle text retained its old event binding",
        )
        assert presentation.layout.current.snapshot_id != first.snapshot_id
        assert presentation.cue.current.boxes

        assert presentation.toggle_renderer()

        assert ipc.query("subtitle-layout") is False


@pytest.mark.parametrize("change", ["resize", "track-disabled"])
def test_native_layout_follows_live_render_space_and_track_changes(change):
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "mpv", os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_tmp, session, ipc):
        presentation = session.graph.subtitle_presentation
        before = presentation.layout.current
        if change == "resize":
            ipc.command("set_property", "window-scale", 0.5)
            poll_until(
                session,
                lambda: (
                    presentation.layout.current is not None
                    and presentation.layout.current.size != before.size
                ),
                "resize retained the old native layout space",
            )
            size = presentation.layout.current.size
            osd = ipc.query("osd-dimensions")
            assert size == (osd["w"], osd["h"])
            assert presentation.cue.current.boxes
        else:
            ipc.command("set_property", "sid", "no")
            poll_until(
                session,
                lambda: not presentation.cue.current.boxes,
                "disabling the subtitle track retained scan regions",
            )
            assert presentation.layout.current is None


@pytest.mark.skipif(
    not os.environ.get("SAITENKA_LAYOUT_STOCK_MPV"), reason="stock-build control not configured"
)
def test_auto_falls_back_to_shadow_on_a_real_unsupported_player(monkeypatch):
    spans = record_spans(monkeypatch)
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "auto", os.environ["SAITENKA_LAYOUT_STOCK_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_tmp, session, ipc):
        presentation = session.graph.subtitle_presentation
        records = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_source"]
        assert records[-1] == IsPartialDict(
            configured_source="auto",
            selected_source="shadow",
            scan_source="shadow",
            paint_source="shadow",
            reason="unsupported-api",
        )
        assert presentation.layout.supported is False
        assert not presentation.using_layout
        assert presentation.cue.current.boxes[0].hit_regions is None
        assert presentation.cue.current.paint_allowed
        assert ipc.query("sub-visibility") is True


@pytest.mark.parametrize("prefix", [r"{\frz30}", r"{\clip(0,0,200,200)}", r"{\move(0,0,100,100)}"])
def test_native_layout_refuses_unrepresented_geometry_without_hiding_subtitles(prefix):
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, prefix + "猫を見る"),),
        layout=LayoutLiveOptions(
            "mpv",
            os.environ["SAITENKA_LAYOUT_MPV"],
            ("--sub-ass-override=no",),
            require_boxes=False,
        ),
    ) as (_tmp, session, ipc):
        presentation = session.graph.subtitle_presentation
        assert presentation.layout is not None
        poll_until(
            session,
            lambda: presentation.layout.status == "layout-geometry-profile",
            "native geometry refusal was not reported",
        )

        assert presentation.cue.current.boxes == []
        assert ipc.query("sub-visibility") is True
