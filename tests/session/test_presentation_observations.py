from __future__ import annotations

import logging

from session_builder import build_session
from util import FakeIPC

from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import NullRenderer


class ObservedOnlyIPC(FakeIPC):
    reject_reads = False

    def command(self, *args):
        if self.reject_reads and args[0] == "get_property":
            raise AssertionError("presentation issued a synchronous player query")
        return super().command(*args)


def test_resize_uses_observed_dimensions_without_querying_player():
    ipc = ObservedOnlyIPC()
    session = build_session(
        ipc,
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
        options=ReaderOptions().with_overrides(prefetch=False),
    )
    session.graph.playback.install_seed({"osd-dimensions": {"w": 1920, "h": 1080}})
    ipc.reject_reads = True
    generation = session.graph.subtitle_presentation.pipeline.generation

    changed = session.graph.presentation.refresh_osd()

    assert changed
    assert session.graph.screen.osd == (1920, 1080)
    assert session.graph.subtitle_presentation.pipeline.generation > generation


def test_seed_diagnostic_uses_cached_video_even_when_dimensions_are_unchanged(caplog):
    ipc = ObservedOnlyIPC()
    session = build_session(ipc)
    dimensions = {"w": session.graph.screen.osd[0], "h": session.graph.screen.osd[1]}
    session.graph.playback.install_seed(
        {
            "osd-dimensions": dimensions,
            "video-out-params": {"dh": 1080},
        }
    )
    ipc.reject_reads = True

    with caplog.at_level(logging.INFO):
        session.graph.presentation.probe_display_sources("seed", dimensions)

    assert "display sources (seed)" in caplog.text
    assert "1080" in caplog.text


def test_preview_source_keeps_admitted_playhead_and_path_without_player_reads():
    ipc = ObservedOnlyIPC()
    session = build_session(ipc)
    session.graph.playback.install_seed({"path": "/Show - 03.mkv", "time-pos": 63.0})
    ipc.reject_reads = True
    captured = session.graph.preview_commands.card_source()
    session.graph.playback.install_seed({"path": "/Other - 02.mkv", "time-pos": 120.0})

    assert captured.video_path() == "/Show - 03.mkv"
    assert captured.provenance(captured.video_path()).endswith("ep03 · 01:03")
    assert not captured.current()
