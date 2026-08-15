from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app.config import ReaderOptions, SubtitleGeometryOptions
from saitenka.app.controller import Reader
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
            "video-out-params": {"dw": 1280, "dh": 720, "w": 1280, "h": 720, "par": 1.0},
        }

    def command(self, *args):
        self.commands.append(args)
        return {"data": self.props.get(args[1]) if args and args[0] == "get_property" else None}

    def close(self) -> None:
        pass


class FakeBackend:
    def __init__(self) -> None:
        self.requests: list[GeometryRequest] = []
        self.closed = False

    def render(self, request: GeometryRequest) -> GeometrySnapshot:
        self.requests.append(request)
        tokens = tuple(
            TokenGeometry(entry.token_index, Rect(100 + entry.token_index * 60, 600, 50, 40))
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
    assert ("set_property", "sub-visibility", True) in ipc.commands
    assert not any(command and command[0] in {"sub-add", "sub-remove"} for command in ipc.commands)
    assert not any(
        command[:2] == ("set_property", "sid") for command in ipc.commands if len(command) >= 2
    )
    result.close()
    assert backend.closed


def test_native_visibility_is_reasserted_after_track_reconfigure(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)

    result.subtitle_pipeline.activate(result)
    result.subtitle_pipeline.activate(result)

    commands = [
        command for command in ipc.commands if command == ("set_property", "sub-visibility", True)
    ]
    assert len(commands) == 2
    result.close()


def test_provider_failure_keeps_native_track_visible_and_clears_hits(tmp_path: Path) -> None:
    result, ipc, _backend = reader(tmp_path)
    assert result.native_geometry is not None
    result.native_geometry.set_source(tmp_path / "missing.ass")

    result.set_subtitle("猫を見る")
    assert result.native_geometry.worker.wait_idle()
    result.native_geometry.apply(result)

    assert result.boxes == []
    assert result.subtitle_pipeline.last_error is not None
    assert result.native_geometry.status.fallback_reason == "geometry-provider-failed"
    assert ("set_property", "sub-visibility", True) in ipc.commands
    result.close()


def test_non_ass_source_has_stable_fallback_reason(tmp_path: Path) -> None:
    result, _ipc, _backend = reader(tmp_path)
    assert result.native_geometry is not None

    result.native_geometry.set_source(tmp_path / "episode.srt")

    assert result.native_geometry.status.fallback_reason == "subtitle-source-not-authored-ass"
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
