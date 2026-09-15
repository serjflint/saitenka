"""Native layout decoding and acquisition at the renderer/IPC boundaries."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from dirty_equals import IsPartialDict
from saitenka_subtitles.mpv_layout import decode_layout, token_regions
from saitenka_tokenize.japanese import Token
from util import FakeIPC, record_spans

from saitenka.app.mpv_layout_source import LayoutPorts, MpvLayoutSource, layout_boxes
from saitenka.app.native_subtitles import GeometryObservation
from saitenka.app.subtitle_pipeline import SubtitleModeCoordinator
from saitenka.app.subtitle_render import NullRenderer
from saitenka.app.subtitles import token_at


def payload():
    return {
        "available": True,
        "status": "ok",
        "snapshot_id": "g1-r2",
        "revision": 2,
        "capabilities": ["events", "unit-logical-rects", "event-geometry-profile"],
        "media": {"playlist_entry_id": 1},
        "track": {
            "id": 2,
            "decoder_generation": 1,
            "format": "ass",
            "layout_engine": "libass",
            "layout_unit_mode": "harfbuzz-cluster",
        },
        "space": {
            "name": "osd",
            "width": 1280,
            "height": 720,
            "render_origin": "output-render",
            "rect_semantics": "half-open",
            "display_par": 1,
            "margin_top": 0,
            "margin_bottom": 0,
            "margin_left": 0,
            "margin_right": 0,
        },
        "time": {"event_domain": "subtitle", "render_video_pts": 1.5, "render_subtitle_pts": 1.5},
        "events": [
            {
                "text": "猫犬",
                "start": 1.0,
                "end": 3.0,
                "geometry_profile": "static-logical-v1",
                "text_index_unit": "utf8-byte",
                "units": [
                    {
                        "start": 0,
                        "end": 3,
                        "line": 0,
                        "logical_rects": [{"x": 100, "y": 100, "w": 40, "h": 40}],
                    },
                    {
                        "start": 3,
                        "end": 6,
                        "line": 1,
                        "logical_rects": [{"x": 200, "y": 150, "w": 40, "h": 40}],
                    },
                ],
            }
        ],
    }


def test_captured_v3_payload_maps_the_original_japanese_token_spans():
    captured = (Path(__file__).parent / "data/mpv-layout-v3.json").read_bytes()
    assert (
        hashlib.sha256(captured).hexdigest()
        == "62a3af6f2f1bd29a0e06cb2e18c5080062a0fcb9d398b2d84678de51379d5279"
    )
    layout = decode_layout(json.loads(captured)["snapshot"])

    regions = token_regions(layout, ((0, 1), (1, 2), (2, 4)))

    assert layout.text == "猫を見る"
    assert all(regions)
    assert (layout.start, layout.end) == (0.0, 8.0)


@pytest.mark.parametrize("text", ["か\u3099猫", "אב猫", "😀猫"])
def test_byte_mapping_preserves_combining_bidi_and_astral_clusters(text):
    data = payload()
    data["events"][0]["text"] = text
    split = len(text[:-1].encode())
    units = data["events"][0]["units"]
    units[0]["end"] = split
    units[1]["start"] = split
    units[1]["end"] = len(text.encode())
    layout = decode_layout(data)

    regions = token_regions(layout, ((0, len(text) - 1), (len(text) - 1, len(text))))

    assert len(regions) == 2
    assert all(len(group) == 1 for group in regions)


def test_unit_budget_refuses_an_oversized_payload_before_mapping():
    data = payload()
    data["events"][0]["units"] *= 2049

    with pytest.raises(ValueError, match="layout-unit-count"):
        decode_layout(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", True),
        ("revision", -1),
        ("available", 1),
        ("capabilities", []),
        ("snapshot_id", ""),
        ("events", []),
        ("events", [None, None]),
    ],
)
def test_malformed_layout_cannot_be_used_for_scanning(field, value):
    data = payload()
    data[field] = value
    with pytest.raises((ValueError, TypeError)):
        decode_layout(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("start", 1),
        ("end", 2),
        ("logical_rects", [{"x": float("nan"), "y": 0, "w": 1, "h": 1}]),
    ],
)
def test_invalid_utf8_or_coordinates_are_refused(field, value):
    data = payload()
    data["events"][0]["units"][0][field] = value
    with pytest.raises(ValueError):
        decode_layout(data)


def test_a_token_cannot_split_a_renderer_cluster():
    data = payload()
    data["events"][0]["units"] = [
        {
            "start": 0,
            "end": 6,
            "line": 0,
            "logical_rects": [{"x": 100, "y": 100, "w": 80, "h": 40}],
        }
    ]
    snapshot = decode_layout(data)

    assert token_regions(snapshot, ((0, 1), (1, 2))) == ((), ())
    assert len(token_regions(snapshot, ((0, 2),))[0]) == 1


class LayoutIPC(FakeIPC):
    def __init__(self):
        super().__init__()
        self.layout = payload()
        self.hold = False
        self.pending = []
        self.props.update(
            {
                "subtitle-layout": True,
                "options/subtitle-layout": True,
                "subtitle-layout-revision": 2,
                "sid": 2,
                "sub-start": 1.0,
                "sub-end": 3.0,
                "playlist": [{"id": 1, "current": True}],
                "path": "episode.mkv",
            }
        )

    def command(self, *args):
        if args[0] == "set_property":
            self.props[args[1]] = args[2]
        if args[0] == "subtitle-layout":
            self.commands.append(args)
            return {"data": deepcopy(self.layout), "error": "success"}
        if args[0] == "subtitle-layout-valid":
            self.commands.append(args)
            return {"data": args[1] == self.layout["snapshot_id"], "error": "success"}
        return super().command(*args)

    def submit_runtime_mpv(self, **kwargs):
        if self.hold:
            self.pending.append(kwargs)
            return True
        return super().submit_runtime_mpv(**kwargs)


def source():
    ipc = LayoutIPC()
    tokens = [Token("猫犬", "猫犬", "", "名詞", 0, 2)]
    seen = GeometryObservation(
        prop=ipc.props.get,
        osd=(1280, 720),
        text="猫犬",
        tokens=tokens,
        lines=[tokens],
        index=None,
        normalise=str,
        nav_index=-1,
        cue_hint=None,
        cue_revision=1,
        is_skippable=lambda _: False,
    )
    frames, ticks = [], []
    ports = LayoutPorts(
        observe=lambda: seen,
        clear=frames.clear,
        publish=lambda boxes: frames.append(boxes) is None,
        reschedule=lambda: ticks.append(None),
        permitted=lambda: True,
        unavailable=lambda: None,
        changed=lambda: None,
    )
    producer = MpvLayoutSource(ipc, SubtitleModeCoordinator(NullRenderer()), ports, formats="all")
    producer.refresh()
    producer.refresh()
    return producer, ipc, frames, ticks


def test_native_regions_keep_the_gap_between_wrapped_parts_unscannable():
    producer, _ipc, frames, _ticks = source()
    boxes = frames[-1]

    assert producer.status == "scan-only"
    assert token_at(boxes, (120, 120), (0, 0), is_skippable=lambda _: False) == 0
    assert token_at(boxes, (220, 170), (0, 0), is_skippable=lambda _: False) == 0
    assert token_at(boxes, (170, 145), (0, 0), is_skippable=lambda _: False) == -1


def test_hard_line_breaks_keep_line_local_tokens_bound_to_their_own_regions():
    data = payload()
    data["events"][0]["text"] = "猫\n\n犬"
    data["events"][0]["units"][1].update(start=5, end=8)
    first = Token("猫", "猫", "", "名詞", 0, 1)
    second = Token("犬", "犬", "", "名詞", 0, 1)
    seen = GeometryObservation(
        prop=lambda _: None,
        osd=(1280, 720),
        text="猫\n\n犬",
        tokens=[first, second],
        lines=[[first], [second]],
        index=None,
        normalise=str,
        nav_index=-1,
        cue_hint=None,
        cue_revision=1,
        is_skippable=lambda _: False,
    )

    boxes = layout_boxes(decode_layout(data), seen)

    assert token_at(boxes, (120, 120), (0, 0), is_skippable=lambda _: False) == 0
    assert token_at(boxes, (220, 170), (0, 0), is_skippable=lambda _: False) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sid", 3),
        ("sub-start", 2.0),
        ("sub-end", 4.0),
        ("playlist", [{"id": 2, "current": True}]),
        ("subtitle-layout-revision", 3),
    ],
)
def test_event_binding_refuses_another_track_cue_media_or_revision(field, value):
    producer, ipc, frames, _ticks = source()
    ipc.props[field] = value

    producer.input_changed()
    producer.refresh()

    assert not frames
    assert producer.status == "unbound-event"


def test_a_revision_burst_clears_hits_and_keeps_one_fetch_in_flight():
    producer, ipc, frames, _ticks = source()
    ipc.hold = True
    producer.refresh()

    for revision in range(3, 100):
        ipc.props["subtitle-layout-revision"] = revision
        producer.input_changed()
        producer.refresh()

    assert frames == []
    assert len(ipc.pending) == 1


@pytest.mark.parametrize("boundary", ["revision", "connection", "close"])
def test_a_validation_completion_cannot_republish_retired_hits(boundary):
    producer, ipc, frames, _ticks = source()
    ipc.hold = True
    producer.refresh()
    ipc._submit_inline(**ipc.pending.pop(0))
    validation = ipc.pending.pop(0)
    if boundary == "revision":
        producer.input_changed()
    elif boundary == "connection":
        producer.connection_replaced()
    else:
        producer.close()

    ipc._submit_inline(**validation)

    assert frames == []


def test_close_leaves_an_already_enabled_collection_option_alone():
    producer, ipc, _frames, _ticks = source()

    producer.close()

    assert ipc.props["subtitle-layout"] is True


def test_external_reenable_resumes_scanning():
    producer, ipc, frames, _ticks = source()
    ipc.props["options/subtitle-layout"] = False
    producer.input_changed()
    producer.refresh()
    assert producer.status == "disabled-externally"
    assert not frames

    ipc.props["options/subtitle-layout"] = True
    producer.input_changed()
    producer.refresh()

    assert frames
    assert producer.status == "scan-only"


def test_suspension_restores_an_enable_accepted_before_its_completion():
    producer, ipc, frames, _ticks = source()
    ipc.props["subtitle-layout"] = False
    producer.connection_replaced()
    ipc.hold = True
    producer.refresh()
    ipc._submit_inline(**ipc.pending.pop(0))
    ipc._submit_inline(**ipc.pending.pop(0))
    enable = ipc.pending.pop(0)
    # The server has applied the write but its reply is still in flight.
    ipc.command(*enable["command"])

    producer.suspend()

    assert ipc.props["subtitle-layout"] is False
    assert frames == []


@pytest.mark.parametrize("unknown_command", [False, True])
def test_missing_api_is_cached_until_a_new_connection(unknown_command, monkeypatch):
    producer, ipc, frames, _ticks = source()
    original = ipc.command

    def command(*args):
        if args[0] == "subtitle-layout":
            ipc.commands.append(args)
            return (
                {"error": "invalid command"} if unknown_command else {"data": {"capabilities": []}}
            )
        return original(*args)

    monkeypatch.setattr(ipc, "command", command)
    producer.refresh()
    count = len(ipc.commands)
    for _ in range(10):
        producer.refresh()

    assert producer.supported is False
    assert producer.status == "unsupported-api"
    assert not frames
    assert len(ipc.commands) == count


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_native_source_publishes_through_the_real_session_and_retires_on_revision(monkeypatch):
    spans = record_spans(monkeypatch)
    from session_builder import build_session
    from test_native_subtitles import _ExistsDS

    from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
    from saitenka.app.session.factory import SessionServices

    ipc = LayoutIPC()
    ipc.props.update({"osd-dimensions": {"w": 1280, "h": 720}, "pause": True})
    session = build_session(
        ipc,
        options=ReaderOptions(
            subtitle_geometry=SubtitleGeometryOptions(native_visible=True, source="mpv")
        ),
        services=SessionServices(dictionaries=_ExistsDS()),
    )
    try:
        session.graph.playback.install_seed(ipc.props)
        session.graph.playback.observe("sub-text", "猫犬")
        session.graph.cue.settle()
        for _ in range(8):
            ipc.fire_runtime_timer("subtitle:geometry-refresh")
            session.pump()
        presentation = session.graph.subtitle_presentation
        assert presentation.native is None
        assert presentation.layout is not None
        assert presentation.layout.status == "scan-only"
        assert presentation.cue.current.paint_allowed is False
        assert session.graph.tooltip.hit(120, 120) == 0
        records = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_source"]
        assert records[-1] == IsPartialDict(
            configured_source="mpv",
            selected_source="mpv",
            scan_source="mpv",
            paint_source="none",
            paint_allowed=False,
            paint_reason="scan-only-policy",
        )

        session.graph.playback.observe("subtitle-layout-revision", 3)

        assert session.graph.tooltip.hit(120, 120) == -1
        assert presentation.cue.current.boxes == []
        records = [span["attrs"] for span in spans if span["name"] == "subtitle_geometry_source"]
        assert records[-1] == IsPartialDict(
            scan_source="none", paint_source="none", reason="invalidated"
        )
    finally:
        session.close()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("events", [], "layout-event-count"),
        ("capabilities", [], "unsupported-api"),
        ("available", False, "layout-unavailable"),
        ("available", True, "scan-only"),
    ],
)
def test_acquisition_outcome_survives_the_text_free_report(monkeypatch, field, value, reason):
    from saitenka.app.subtitle_report import geometry_records

    producer, ipc, _frames, _ticks = source()
    spans = record_spans(monkeypatch)
    ipc.layout[field] = value

    producer.input_changed()
    producer.refresh()

    records = geometry_records(
        [{"name": span["name"], "ph": "X", "args": span["attrs"]} for span in spans]
    )
    assert records[-1]["args"] == IsPartialDict(
        geometry_source="mpv",
        reason=reason,
        attempt=3,
        cue_revision=1,
        capability="false" if reason == "unsupported-api" else "true",
    )
    assert "猫" not in json.dumps(records, ensure_ascii=False)


def test_cached_api_refusal_remains_explainable_after_invalidation(monkeypatch):
    producer, ipc, _frames, _ticks = source()
    ipc.layout["capabilities"] = []
    producer.refresh()
    spans = record_spans(monkeypatch)

    producer.input_changed()
    producer.refresh()

    assert spans[-1]["attrs"] == IsPartialDict(reason="unsupported-api", capability="false")


def test_stale_acquisition_retains_request_identity(monkeypatch):
    producer, ipc, _frames, _ticks = source()
    spans = record_spans(monkeypatch)
    ipc.hold = True
    producer.refresh()
    ipc._submit_inline(**ipc.pending.pop(0))
    validation = ipc.pending.pop(0)
    producer.input_changed()

    ipc._submit_inline(**validation)

    start, finish = spans[0]["attrs"], spans[-1]["attrs"]
    assert finish == IsPartialDict(
        reason="stale",
        attempt=start["attempt"],
        request_generation=start["generation"],
        request_cue_revision=start["cue_revision"],
    )
    assert finish["generation"] > finish["request_generation"]
