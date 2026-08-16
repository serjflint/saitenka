from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from dirty_equals import IsPartialDict

from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
from saitenka.app.controller import Reader
from saitenka.app.subtitle_render import NativeVisibleRenderer
from saitenka.app.tokenize import Token
from saitenka.subtitles import Cue, CueIndex, GeometryRequest, GeometrySnapshot, Rect, TokenGeometry

if TYPE_CHECKING:
    from pathlib import Path

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る
""".encode()

ASS_TWO = (
    ASS.decode()
    .replace(
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n",
        "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,猫を見る\n"
        "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,犬も見る\n",
    )
    .encode()
)


class FakeIPC:
    def __init__(self) -> None:
        self.commands: list[tuple] = []
        self.props = {
            "sid": 2,
            "sub-start": 1.0,
            "sub-end": 3.0,
            "time-pos": 1.25,
            "pause": True,
            "osd-dimensions": {"w": 1280, "h": 720},
            "video-out-params": {"dw": 1280, "dh": 720, "w": 1280, "h": 720, "par": 1.0},
            "options/sub-ass-override": "no",
            "options/sub-ass-scale-with-window": False,
            "options/sub-scale": 1.0,
            "options/sub-pos": 100.0,
            "options/sub-use-margins": True,
            "options/sub-ass-force-margins": False,
            "options/sub-ass-video-aspect-override": 0.0,
            "options/sub-ass-use-video-data": "all",
            "options/sub-ass-vsfilter-aspect-compat": None,
            "options/sub-ass-style-overrides": [],
            "options/sub-font-provider": "auto",
            "options/embeddedfonts": False,
            "options/sub-fonts-dir": "",
        }
        self.set_property_error: str | None = None
        self.set_property_exception: Exception | None = None

    def command(self, *args):
        self.commands.append(args)
        if args and args[0] == "get_property":
            return {"error": "success", "data": self.props.get(args[1])}
        if args and args[0] == "set_property" and self.set_property_exception is not None:
            raise self.set_property_exception
        if args and args[0] == "set_property" and self.set_property_error is not None:
            return {"error": self.set_property_error}
        return {"error": "success", "data": None}

    def close(self) -> None:
        pass


class FakeBackend:
    def __init__(self) -> None:
        self.requests: list[GeometryRequest] = []
        self.closed = False
        self.error: Exception | None = None
        self.token_index_offset = 0

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        tokens = tuple(
            TokenGeometry(
                entry.token_index + self.token_index_offset,
                Rect(100 + entry.token_index * 60, 600, 50, 40),
            )
            for entry in request.palette
        )
        return GeometrySnapshot(
            request.generation,
            request.track_id,
            request.event_id,
            request.timestamp_ms,
            request.variant,
            tokens,
        )

    def close(self) -> None:
        self.closed = True


class _SingleTokenizer:
    name = "single"

    def tokenize(self, line, *, strip_furigana=True, merge=True):  # noqa: ARG002
        return [Token(surface=line, lemma=line, reading="", pos="名詞", start=0, end=len(line))]

    def is_content(self, _token):
        return True

    def is_skippable(self, token):
        return not token.surface.strip()

    def query_token(self, _query):
        return None

    def inflected_in(self, tokens, index):
        return tokens[index].surface

    def phrase_terms(self, _tokens, _index, _has_term):
        return None


class _MismatchedTokenizer(_SingleTokenizer):
    name = "mismatched"

    def tokenize(self, line, *, strip_furigana=True, merge=True):  # noqa: ARG002
        return [Token(surface="different", lemma=line, reading="", pos="名詞", start=0, end=1)]


def reader(tmp_path: Path) -> tuple[Reader, FakeIPC, FakeBackend]:
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS)
    ipc = FakeIPC()
    backend = FakeBackend()
    options = ReaderOptions(
        subtitle_geometry=SubtitleGeometryOptions(native_visible=True),
        prefetch=False,
    )
    result = Reader(ipc, options=options, geometry_backend=backend)
    assert result.native_geometry is not None
    result.native_geometry.set_source(source)
    return result, ipc, backend


def test_native_visible_mode_never_adds_or_selects_generated_track(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)

    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)

    assert backend.requests
    assert [box.index for box in result.boxes] == list(range(len(result.tokens)))
    result.hover = 0
    result._draw_subtitle()
    focus = [
        command for command in ipc.commands if command[:3] == ("osd-overlay", 1001, "ass-events")
    ]
    assert len(focus) == 1
    assert focus[0][4:6] == (1280, 720)
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert not any(command and command[0] in {"sub-add", "sub-remove"} for command in ipc.commands)
    assert not any(
        command[:2] == ("set_property", "sid") for command in ipc.commands if len(command) >= 2
    )
    result.close()
    assert backend.closed


def test_native_visibility_is_reasserted_after_track_reconfigure(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    ipc.commands.clear()

    result.subtitle_pipeline.activate(result)
    result.subtitle_pipeline.activate(result)

    commands = [
        command for command in ipc.commands if command == ("set_property", "sub-visibility", True)
    ]
    assert len(commands) == 2
    result.close()


def test_provider_failure_restores_legacy_renderer_and_hits(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    assert result.native_geometry is not None
    result.native_geometry.set_source(tmp_path / "missing.ass")

    result.set_subtitle("猫を見る")
    assert result.native_geometry.worker.wait_idle()
    result.native_geometry.apply(result)

    assert result.native_geometry.status.fallback_reason == "geometry-provider-failed"
    assert ("set_property", "sub-visibility", False) in ipc.commands
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    result._sub_pending = None
    result._draw_subtitle()
    assert result.boxes
    result.close()


def test_provider_failure_preserves_hover_pause_during_renderer_fallback(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    result._sub_pending = None
    result.hover = 0
    result._paused_by_tip = True
    ipc.commands.clear()
    backend.error = RuntimeError("font provider unavailable")
    result.subtitle_pipeline.invalidate()
    result.native_geometry.worker.invalidate_cache()

    assert result.native_geometry.schedule(result)
    assert result.native_geometry.worker.wait_idle()
    assert not result.native_geometry.apply(result)

    assert ("set_property", "pause", False) not in ipc.commands
    assert result.hover == 0
    assert result.boxes
    box = result.boxes[0]
    assert result._hit(result.sub_origin[0] + box.x + 1, result.sub_origin[1] + box.y + 1) == 0
    result._paused_by_tip = False
    result.close()


def test_non_ass_source_uses_legacy_renderer_with_hits(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    assert result.native_geometry is not None

    result.native_geometry.set_source(tmp_path / "episode.srt", reader=result)
    result.set_subtitle("猫を見る")

    assert result.native_geometry.status.fallback_reason == "subtitle-source-not-authored-ass"
    assert backend.requests == []
    assert ("set_property", "sub-visibility", False) in ipc.commands
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    result._sub_pending = None
    result._draw_subtitle()
    assert result.boxes
    result.close()


def test_fallback_transition_records_one_bounded_metric(tmp_path: Path) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    from saitenka import otel_metrics

    result, _ipc, _backend = reader(tmp_path)
    metric_reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[metric_reader])
    otel_metrics.register(metric_reader, provider.get_meter("test"))
    try:
        assert result.native_geometry is not None
        source = tmp_path / "episode.srt"
        result.native_geometry.set_source(source, reader=result)
        result.native_geometry.set_source(source, reader=result)

        assert otel_metrics.snapshot()["saitenka.subtitle_geometry.fallbacks"]["value"] == 1
    finally:
        result.close()
        otel_metrics.unregister()
        provider.shutdown()


def test_ass_geometry_replaces_legacy_fallback_after_source_switch(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    assert result.native_geometry is not None
    result.native_geometry.set_source(tmp_path / "episode.srt", reader=result)
    result.set_subtitle("猫を見る")
    result._sub_pending = None
    result._draw_subtitle()
    assert result.boxes

    source = tmp_path / "episode.ass"
    source.write_bytes(ASS)
    result.native_geometry.set_source(source, reader=result)
    result.set_subtitle("猫を見る")
    assert result.native_geometry.worker.wait_idle()

    assert result.native_geometry.apply(result)
    assert backend.requests
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert result.native_geometry.status.fallback_reason is None
    result.close()


def test_next_static_cue_uses_ready_lookahead_without_new_render(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS_TWO)
    result._sub_index = CueIndex((Cue(1.0, 3.0, "猫を見る"), Cue(4.0, 6.0, "犬も見る")))
    assert result.native_geometry is not None

    result.set_subtitle("猫を見る")
    assert result.native_geometry.worker.wait_idle()
    assert len(backend.requests) == 2
    ipc.props.update({"sub-start": 4.0, "sub-end": 6.0, "time-pos": 4.2})

    result.set_subtitle("犬も見る")

    assert result.native_geometry.apply(result)
    assert len(backend.requests) == 2
    stats = result.native_geometry.worker.stats
    assert (stats.ready_before_presented, stats.presented) == (1, 2)
    result.close()


def test_instant_navigation_uses_target_cue_timing_not_stale_mpv_properties(tmp_path: Path) -> None:
    result, _ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(ASS_TWO)
    result._geometry_cue_hint = Cue(4.0, 6.0, "犬も見る")

    result.set_subtitle("犬も見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()

    assert backend.requests[0].event_id.start_ms == 4_000
    result.close()


def test_source_clear_is_a_generation_boundary_and_restores_legacy_renderer(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    old_generation = result.subtitle_pipeline.generation

    result.native_geometry.set_source(None, reader=result)

    assert result.subtitle_pipeline.generation == old_generation + 1
    assert result.subtitle_pipeline.current is None
    assert ("osd-overlay", 1001, "none", "") in ipc.commands
    assert ("set_property", "sub-visibility", False) in ipc.commands
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    assert not result.native_geometry.apply(result)
    result.close()


def test_tokenizer_change_rebuilds_geometry_after_new_tokens_land(tmp_path: Path) -> None:
    result, _ipc, backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    assert len(result.boxes) > 1

    result.use_tokenizer(_SingleTokenizer())
    result._retokenize_current_cue()

    assert result.boxes == []
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    assert len(result.boxes) == len(backend.requests[-1].palette) == 1
    result.close()


def test_mismatched_token_annotation_fails_closed(tmp_path: Path) -> None:
    result, _ipc, backend = reader(tmp_path)
    result.use_tokenizer(_MismatchedTokenizer())

    result.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.boxes == []
    assert result.native_geometry is not None
    assert result.native_geometry.status.fallback_reason == "subtitle-token-annotation-invalid"
    result.close()


def test_unexpected_geometry_error_restores_legacy_renderer(tmp_path: Path, monkeypatch) -> None:
    result, ipc, backend = reader(tmp_path)
    assert result.native_geometry is not None

    def fail_render_inputs(_reader):
        raise RuntimeError("unexpected profile failure")

    monkeypatch.setattr(result.native_geometry, "_render_inputs", fail_render_inputs)
    result.set_subtitle("猫を見る")
    result._sub_pending = None
    result._draw_subtitle()

    assert backend.requests == []
    assert result.native_geometry.status.fallback_reason == "geometry-provider-failed"
    assert result.boxes
    assert ("set_property", "sub-visibility", False) in ipc.commands
    result.close()


def test_provider_error_is_consumed_once_and_cleared_by_source_switch(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("boom")
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()

    assert not result.native_geometry.apply(result)
    clears = ipc.commands.count(("osd-overlay", 1001, "none", ""))
    assert not result.native_geometry.apply(result)
    assert ipc.commands.count(("osd-overlay", 1001, "none", "")) == clears

    result.native_geometry.set_source(None, reader=result)
    assert result.subtitle_pipeline.last_error is None
    assert result.native_geometry.status.fallback_reason == "subtitle-source-unavailable"
    result.close()


def test_repeated_provider_failure_emits_one_transition_diagnostic(tmp_path: Path, caplog) -> None:
    result, _ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")
    assert result.native_geometry is not None
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="saitenka.app.native_subtitles"):
        result.set_subtitle("猫を見る")
        assert result.native_geometry.worker.wait_idle()
        result.native_geometry.apply(result)
        result.subtitle_pipeline.invalidate()
        result.native_geometry.worker.invalidate_cache()
        assert result.native_geometry.schedule(result)
        assert result.native_geometry.worker.wait_idle()
        result.native_geometry.apply(result)

    assert [record.getMessage() for record in caplog.records] == [
        (
            "native subtitle geometry unavailable; using Saitenka renderer: "
            "geometry-provider-failed "
            "(font provider unavailable)"
        )
    ]
    result.close()


def test_invalid_result_identity_removes_visible_native_focus(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    result.hover = 0
    result._draw_subtitle()
    focus_index = len(ipc.commands)

    backend.token_index_offset = len(result.tokens)
    result.subtitle_pipeline.invalidate()
    result.native_geometry.worker.invalidate_cache()
    assert result.native_geometry.schedule(result)
    assert result.native_geometry.worker.wait_idle()

    assert not result.native_geometry.apply(result)
    assert result.boxes == []
    assert ("osd-overlay", 1001, "none", "") in ipc.commands[focus_index:]
    result.close()


def test_empty_cue_removes_visible_native_focus(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    result.hover = 0
    result._draw_subtitle()

    result.set_subtitle("")

    assert result.boxes == []
    assert ("osd-overlay", 1001, "none", "") in ipc.commands
    result.close()


def test_close_removes_visible_native_focus(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    result.hover = 0
    result._draw_subtitle()

    result.close()

    assert ("osd-overlay", 1001, "none", "") in ipc.commands


def test_repeated_text_event_refreshes_geometry_when_timing_changes(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    source = tmp_path / "episode.ass"
    source.write_bytes(
        ASS.replace(
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n",
            b"Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n"
            b"Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,\xe7\x8c\xab\xe3\x82\x92\xe8\xa6\x8b\xe3\x82\x8b\n",
        )
    )
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)

    ipc.props.update({"sub-start": 4.0, "sub-end": 6.0, "time-pos": 4.2})
    result._on_property_change({"name": "sub-start", "data": 4.0})
    result._on_property_change({"name": "sub-end", "data": 6.0})
    assert result.boxes == []
    assert result.native_geometry.worker.wait_idle()

    assert backend.requests[-1].event_id.start_ms == 4_000
    assert result.native_geometry.apply(result)
    result.close()


def test_custom_mpv_subtitle_settings_report_mismatched_inputs(tmp_path: Path, caplog) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-scale"] = 1.2
    ipc.props["options/sub-font-provider"] = "fontconfig"
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="saitenka.app.native_subtitles"):
        result.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.boxes == []
    assert result.native_geometry is not None
    assert result.native_geometry.status.fallback_reason == "subtitle-render-input-unsupported"
    assert [record.getMessage() for record in caplog.records] == [
        (
            "native subtitle geometry unavailable; using Saitenka renderer: "
            "subtitle-render-input-unsupported "
            "(sub-scale=1.2, sub-font-provider='fontconfig')"
        )
    ]
    result.close()


def test_custom_mpv_font_provider_fails_closed(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-font-provider"] = "fontconfig"

    result.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.boxes == []
    assert result.native_geometry is not None
    assert result.native_geometry.status.fallback_reason == "subtitle-render-input-unsupported"
    result.close()


def test_mpv_empty_style_override_normalization_is_supported(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-style-overrides"] = [""]

    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()

    assert backend.requests
    assert result.native_geometry.apply(result)
    result.close()


def test_retina_letterbox_geometry_uses_mpv_frame_margins(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["osd-dimensions"] = {
        "w": 3024,
        "h": 1898,
        "mt": 98,
        "mb": 99,
        "ml": 0,
        "mr": 0,
    }
    ipc.props["video-out-params"] = {
        "dw": 1920,
        "dh": 1080,
        "w": 1920,
        "h": 1080,
        "par": 1.0,
    }

    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()

    request = backend.requests[-1]
    assert request.frame_size == (3024, 1898)
    assert request.storage_size == (1920, 1080)
    assert request.margins == (98, 99, 0, 0)
    assert request.use_margins is False
    assert result.native_geometry.apply(result)
    result.close()


def test_authored_ass_force_margins_is_forwarded(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-force-margins"] = True

    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()

    assert backend.requests[-1].use_margins is True
    result.close()


def test_authored_ass_margin_policy_change_refreshes_geometry(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)

    ipc.props["options/sub-ass-force-margins"] = True
    result._on_property_change({"name": "options/sub-ass-force-margins", "data": True})
    assert result.native_geometry.worker.wait_idle()

    assert backend.requests[-1].use_margins is True
    assert result.native_geometry.apply(result)
    result.close()


def test_osd_and_video_pixel_aspects_are_composed(tmp_path: Path) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["osd-dimensions"]["par"] = 1.25
    ipc.props["video-out-params"]["par"] = 1.2

    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()

    assert backend.requests[-1].pixel_aspect == pytest.approx(1.5)
    result.close()


def test_ass_video_aspect_override_falls_back_with_observed_value(tmp_path: Path, caplog) -> None:
    result, ipc, backend = reader(tmp_path)
    ipc.props["options/sub-ass-video-aspect-override"] = 1.85
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="saitenka.app.native_subtitles"):
        result.set_subtitle("猫を見る")

    assert backend.requests == []
    assert result.native_geometry is not None
    assert result.native_geometry.status.fallback_reason == "subtitle-render-input-unsupported"
    assert "sub-ass-video-aspect-override=1.85" in caplog.records[-1].getMessage()
    result.close()


def test_non_utf8_ass_has_stable_fallback_reason(tmp_path: Path) -> None:
    result, _ipc, _backend = reader(tmp_path)
    source = tmp_path / "legacy.ass"
    source.write_bytes(ASS + b"\xff")
    assert result.native_geometry is not None
    result.native_geometry.set_source(source, reader=result)

    result.set_subtitle("猫を見る")
    assert result.native_geometry.worker.wait_idle()
    result.native_geometry.apply(result)

    assert result.native_geometry.status.fallback_reason == "subtitle-source-encoding-unsupported"
    result.close()


def test_native_visibility_retries_without_repeating_diagnostic(tmp_path: Path, caplog) -> None:
    result, ipc, _backend = reader(tmp_path)
    ipc.set_property_error = "disconnected"
    renderer = result.subtitle_pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger="saitenka.app.subtitle_render"):
        assert renderer.activate(result) is False
        renderer.draw(result)

    assert ipc.commands.count(("set_property", "sub-visibility", False)) == 2
    assert [record.getMessage() for record in caplog.records] == [
        "mpv rejected subtitle visibility change: disconnected"
    ]
    result.close()


def test_rejected_native_visibility_reassertion_restores_legacy_renderer(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    assert result.native_geometry.apply(result)
    result._sub_pending = None
    ipc.command(
        "set_property",
        "sub-visibility",
        False,  # noqa: FBT003  # raw mpv IPC wire value
    )
    ipc.commands.clear()
    ipc.set_property_error = "disconnected"

    result.subtitle_pipeline.activate(result)

    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    assert result.boxes
    box = result.boxes[0]
    assert result._hit(result.sub_origin[0] + box.x + 1, result.sub_origin[1] + box.y + 1) == 0
    result.close()


def test_native_visibility_exception_keeps_legacy_renderer(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    renderer = result.subtitle_pipeline.renderer
    assert isinstance(renderer, NativeVisibleRenderer)
    ipc.set_property_exception = OSError("pipe closed")

    result.set_subtitle("猫を見る")
    result._sub_pending = None
    result._draw_subtitle()

    assert result.boxes
    assert any(command[0] == "overlay-add" for command in ipc.commands)
    result.close()


def test_native_visibility_rejection_emits_one_fallback_span(tmp_path: Path, monkeypatch) -> None:
    from saitenka import otel_metrics

    spans: list[tuple[str, dict[str, str]]] = []
    traced = otel_metrics.traced

    @contextmanager
    def record_span(name: str, **attributes: str):
        if name == "subtitle_geometry_fallback":
            spans.append((name, attributes))
        with traced(name, **attributes) as span:
            yield span

    monkeypatch.setattr(otel_metrics, "traced", record_span)
    result, ipc, _backend = reader(tmp_path)
    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    ipc.set_property_error = "disconnected"

    assert not result.native_geometry.apply(result)

    assert spans == [("subtitle_geometry_fallback", {"reason": "mpv-sub-visibility-rejected"})]
    result.close()


def test_runtime_telemetry_reports_geometry_worker_health(tmp_path: Path) -> None:
    result, _ipc, backend = reader(tmp_path)
    backend.error = RuntimeError("font provider unavailable")

    result.set_subtitle("猫を見る")
    assert result.native_geometry is not None
    assert result.native_geometry.worker.wait_idle()
    result.native_geometry.apply(result)

    assert result._telemetry_gauges() == IsPartialDict(
        **{
            "subtitle_geometry.submitted": 1.0,
            "subtitle_geometry.completed": 0.0,
            "subtitle_geometry.failures": 1.0,
            "subtitle_geometry.presented": 1.0,
        }
    )
    result.close()
