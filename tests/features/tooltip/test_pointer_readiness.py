"""Pointer input and delayed cue preparation converge without another mouse movement."""

import threading

import pytest
from driver import Driver
from tip_fakes import LinkingDS
from util import FakeIPC

from saitenka.app import track_preparation
from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionServices
from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner


@pytest.mark.parametrize(
    ("arrival", "pointer"),
    [
        ("before-pointer", "stay"),
        ("after-pointer", "stay"),
        ("after-pointer", "leave"),
        ("after-pointer", "new-cue"),
    ],
)
def test_track_preparation_preserves_hover_under_a_stationary_pointer(
    tmp_path, monkeypatch, make_session, arrival, pointer
):
    submitted = []
    monkeypatch.setattr(
        track_preparation,
        "configure_lane",
        lambda *_args: lambda **job: submitted.append(job) or True,
    )
    source = tmp_path / "track.srt"
    source.write_text(
        "1\n00:00:00,000 --> 00:00:08,000\n門前の小僧習わぬ経を読む\n", encoding="utf-8"
    )
    ipc = FakeIPC()
    ipc.props.update(
        {
            "path": "/video.mkv",
            "osd-dimensions": {"w": 1280, "h": 720},
            "sid": 1,
            "sub-text": "門前の小僧習わぬ経を読む",
            "sub-start": 0.0,
            "sub-end": 8.0,
            "track-list": [
                {
                    "id": 1,
                    "type": "sub",
                    "selected": True,
                    "external": True,
                    "external-filename": str(source),
                }
            ],
        }
    )
    reader = make_session(
        ipc,
        options=ReaderOptions(prefetch=False),
        services=SessionServices(dictionaries=LinkingDS()),
    )
    reader.start()
    reader.graph.subtitle_navigation.load_index(source)
    reader.pump()
    job = submitted[0]
    prepared = track_preparation.prepare_track(job["request"], threading.Event())

    def finish():
        job["on_finished"](
            EffectFinished(
                EffectId(1),
                Owner.SUBTITLE,
                job["identity"],
                EffectOutcome.SUCCEEDED,
                result=prepared,
            )
        )
        reader.pump()

    if arrival == "before-pointer":
        finish()
    ui = Driver(reader).move_to_word(0)
    if pointer == "leave":
        ui.leave()
    elif pointer == "new-cue":
        ui.subtitle("犬")
    if arrival == "after-pointer":
        finish()

    assert (ui.tip_shown, ui.hover) == (pointer == "stay", 0 if pointer == "stay" else -1)
