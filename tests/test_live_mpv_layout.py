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


@pytest.mark.parametrize("prefix", ["", r"{\pos(640,650)\fscx90}", r"{\kf100}"])
def test_blended_subtitles_keep_shadow_scanning_and_eligible_color(prefix, monkeypatch):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    spans = record_spans(monkeypatch)
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, prefix + "猫を見る"),),
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])), Palette()),
        layout=LayoutLiveOptions(
            "auto",
            os.environ["SAITENKA_LAYOUT_MPV"],
            ("--sub-ass-override=no", "--blend-subtitles=yes"),
        ),
    ) as (_, session, ipc):
        request = session.graph.cue.draw_request()
        records = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_source"]
        assert records[-1] == IsPartialDict(
            selected_source="shadow",
            scan_source="shadow",
            reason="layout-unsupported-render-mode",
            paint_source="none" if "kf" in prefix else "shadow",
        )
        box = request.boxes[0]
        assert session.graph.tooltip.hit(box.x + box.w // 2, box.y + box.h // 2) == box.index
        assert bool(color_ladder(request).devices) is ("kf" not in prefix)
        assert ipc.query("blend-subtitles") is True


def test_disabling_blending_returns_auto_to_native_scanning():
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "auto",
            os.environ["SAITENKA_LAYOUT_MPV"],
            ("--sub-ass-override=no", "--blend-subtitles=yes"),
        ),
    ) as (_, session, ipc):
        presentation = session.graph.subtitle_presentation
        assert not presentation.using_layout

        ipc.command("set_property", "blend-subtitles", "no")
        poll_until(
            session,
            lambda: presentation.using_layout and bool(presentation.cue.current.boxes),
            "auto did not recover native geometry",
        )

        assert presentation.cue.current.boxes[0].hit_regions


@pytest.mark.parametrize(
    ("cues", "reason"),
    [
        (((0.0, 8.0, r"{\frz20}猫を見る"), (0.0, 8.0, "犬を見る")), "layout-geometry-profile"),
        (((0.0, 8.0, r"{\frz20}猫を見る"),), "layout-geometry-profile"),
    ],
)
def test_auto_preserves_shadow_scanning_for_unsupported_native_geometry(cues, reason, monkeypatch):
    spans = record_spans(monkeypatch)
    with live_reader(
        native_visible=True,
        cues=cues,
        layout=LayoutLiveOptions(
            "auto", os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_, session, _ipc):
        records = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_source"]
        assert records[-1] == IsPartialDict(
            selected_source="shadow", scan_source="shadow", reason=reason
        )
        cue = session.graph.subtitle_presentation.cue.current
        for surface in ("猫", "犬") if len(cues) > 1 else ("猫",):
            index = next(i for i, token in enumerate(cue.tokens) if token.surface == surface)
            box = next(box for box in cue.boxes if box.index == index)
            assert session.graph.tooltip.hit(box.x + box.w // 2, box.y + box.h // 2) == index


@pytest.mark.parametrize(
    ("name", "value"), [("osd-dimensions", {"w": 640, "h": 360}), ("sub-delay", 1.0)]
)
def test_fallback_hits_retire_before_refresh_on_geometry_input_change(name, value):
    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, "猫を見る"),),
        layout=LayoutLiveOptions(
            "auto",
            os.environ["SAITENKA_LAYOUT_MPV"],
            ("--sub-ass-override=no", "--blend-subtitles=yes"),
        ),
    ) as (_, session, _ipc):
        presentation = session.graph.subtitle_presentation
        box = presentation.cue.current.boxes[0]
        point = (box.x + box.w // 2, box.y + box.h // 2)
        assert session.graph.tooltip.hit(*point) == box.index

        session.graph.playback.observe(name, value)

        assert not presentation.cue.current.boxes
        assert session.graph.tooltip.hit(*point) == -1


@pytest.mark.parametrize("source_name", ["auto", "shadow"])
def test_layout_revision_does_not_withdraw_unchanged_shadow_color(source_name):
    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, r"{\pos(640,650)\fscx90}猫を見る"),),
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])), Palette()),
        layout=LayoutLiveOptions(
            source_name, os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_, session, ipc):
        before = session.graph.cue.draw_request()
        assert color_ladder(before).devices

        session.graph.playback.observe(
            "subtitle-layout-revision", (ipc.query("subtitle-layout-revision") or 0) + 1
        )

        after = session.graph.cue.draw_request()
        assert [box.index for box in after.boxes] == [box.index for box in before.boxes]
        assert color_ladder(after).devices == color_ladder(before).devices


@pytest.mark.parametrize("source_name", ["auto", "shadow"])
def test_navigated_static_cue_keeps_color_after_layout_observations_settle(source_name):
    import time

    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    with live_reader(
        native_visible=True,
        cues=(
            (0.0, 2.0, r"{\pos(640,650)\fscx90}猫を見る"),
            (2.0, 5.0, r"{\pos(640,650)\fscx90}犬を見る"),
        ),
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫", "犬"])), Palette()),
        layout=LayoutLiveOptions(
            source_name, os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_, session, _ipc):
        session.graph.subtitle_navigation.navigate(1)
        poll_until(
            session,
            lambda: (
                session.graph.playback.cue.text == "犬を見る"
                and bool(color_ladder(session.graph.cue.draw_request()).devices)
            ),
            "navigation never colored the target cue",
        )
        for _ in range(20):
            session.pump()
            time.sleep(0.01)

        request = session.graph.cue.draw_request()
        assert request.text == "犬を見る"
        assert color_ladder(request).devices
        box = request.boxes[0]
        assert session.graph.tooltip.hit(box.x + box.w // 2, box.y + box.h // 2) == box.index


@pytest.mark.parametrize("source_name", ["auto", "shadow"])
def test_static_speaker_colors_remain_colored_after_observations_settle(source_name):
    import time

    from saitenka_wordstate import Scorer
    from saitenka_wordstate.known import KnownWords

    from saitenka.app.scoring import Coloring, Palette

    with live_reader(
        native_visible=True,
        cues=((0.0, 8.0, r"{\pos(640,650)\fscx50\c&H0000FFFF}猫{\fscx100}を見る"),),
        scorer=Coloring(Scorer(known=KnownWords.from_set(["猫"])), Palette()),
        layout=LayoutLiveOptions(
            source_name, os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_, session, _ipc):
        poll_until(
            session,
            lambda: bool(color_ladder(session.graph.cue.draw_request()).devices),
            "static color never painted",
        )
        for _ in range(20):
            session.pump()
            assert color_ladder(session.graph.cue.draw_request()).devices
            time.sleep(0.01)
        boxes = session.graph.cue.draw_request().boxes
        assert boxes
        assert bool(boxes[0].hit_regions) is (source_name == "auto")


def test_native_overlap_uses_both_events_for_token_hits():
    with live_reader(
        native_visible=True,
        cues=(
            (0.0, 8.0, r"{\pos(320,500)}猫を見る"),
            (0.0, 8.0, r"{\pos(960,650)\c&H0000FFFF}犬を見る"),
        ),
        layout=LayoutLiveOptions(
            "mpv", os.environ["SAITENKA_LAYOUT_MPV"], ("--sub-ass-override=no",)
        ),
    ) as (_, session, _ipc):
        request = session.graph.cue.draw_request()
        assert all(box.hit_regions for box in request.boxes)
        for word in ("猫", "犬"):
            index = next(
                i
                for i, t in enumerate(session.graph.subtitle_presentation.cue.current.tokens)
                if t.surface == word
            )
            box = next(b for b in request.boxes if b.index == index)
            region = box.hit_regions[0]
            assert (
                session.graph.tooltip.hit(region.x + region.width / 2, region.y + region.height / 2)
                == index
            )
