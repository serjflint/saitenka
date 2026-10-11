from __future__ import annotations

import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from util import FakeIPC

from saitenka.app.config import KeyOptions
from saitenka.app.features.preview.miner_ui import CardSource, PreviewPorts
from saitenka.app.features.preview.preparation import PreviewDestination
from saitenka.app.features.preview.preview_controller import PreviewController
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome, Owner


class DeferredIPC(FakeIPC):
    def __init__(self):
        super().__init__()
        self.handlers = {}
        self.jobs = []
        self.accept = True
        self.reject_inline = False

    def register_runtime_job_lane(self, name, _policy, handler):
        if name == "card-preview":
            self.handlers[name] = handler
            return True
        return super().register_runtime_job_lane(name, _policy, handler)

    def submit_runtime_job(self, **job):
        if job["lane"] != "card-preview":
            return super().submit_runtime_job(**job)
        if self.accept:
            self.jobs.append(job)
        elif self.reject_inline:
            job["on_finished"](
                EffectFinished(
                    EffectId(1), Owner.INTERACTION, job["identity"], EffectOutcome.REJECTED
                )
            )
        return self.accept

    def prepare_next(self):
        job = self.jobs.pop(0)
        result = self.handlers[job["lane"]](job["request"], threading.Event())
        return job, EffectFinished(
            EffectId(1), Owner.INTERACTION, job["identity"], EffectOutcome.SUCCEEDED, result
        )

    @staticmethod
    def deliver(prepared):
        job, completion = prepared
        job["on_finished"](completion)


class Surface:
    def present(self, *_args, **_kwargs):
        pass

    def remove(self, _oid):
        pass


def source(*, note_info=None):
    return CardSource(
        "deck",
        "model",
        (("expression", "Expression"),),
        note_info or (lambda ident: {"fields": {"Expression": {"value": str(ident)}}}),
        lambda _name: None,
        lambda _name: None,
        [],
        lambda _video: "scene",
        lambda: "Show.mkv",
    )


def ports(preview, ipc):
    return PreviewPorts(
        preview=preview,
        help_open=False,
        tip_keys_bound=False,
        surfaces=Surface(),
        osd=(1280, 720),
        tip_width=440,
        ipc=ipc,
        keys=KeyOptions(),
        add_duplicate=lambda: None,
        play_audio=False,
    )


def request_existing(
    preview, ipc, *, ident=42, captured=None, destination=None, toast=lambda *_args: None
):
    preview.present_existing(
        lambda: ports(preview, ipc),
        lambda: captured or source(),
        toast,
        ident,
        SimpleNamespace(expression="猫", reading="ねこ", glosses=()),
        "exists",
        enabled=True,
        destination=destination,
    )


def test_disabled_mining_preview_toasts_without_resolving_sources():
    preview = PreviewController(FakeIPC())
    toasts = []

    def unavailable():
        raise AssertionError("disabled preview accessed presentation or data")

    preview.present_existing(
        unavailable,
        unavailable,
        lambda text, *_args: toasts.append(text),
        42,
        SimpleNamespace(expression="猫"),
        "exists",
        enabled=False,
    )

    assert toasts == ["already have 猫"]
    assert preview.state.content is None


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_note_lookup_runs_on_worker_and_publication_uses_fresh_ports():
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    lookup_thread = []
    publication = []
    prepared = []

    def note_info(ident):
        lookup_thread.append(threading.current_thread())
        assert threading.current_thread() is not threading.main_thread()
        return {"fields": {"Expression": {"value": str(ident)}}}

    def live_ports():
        publication.append(threading.current_thread())
        return ports(preview, ipc)

    preview.present_existing(
        live_ports,
        lambda: source(note_info=note_info),
        lambda *_args: None,
        42,
        SimpleNamespace(expression="猫", reading="ねこ", glosses=()),
        "exists",
        enabled=True,
    )
    assert preview.state.content is None
    worker = threading.Thread(target=lambda: prepared.append(ipc.prepare_next()))
    worker.start()
    worker.join(1)
    assert not worker.is_alive()

    ipc.deliver(prepared[0])

    assert preview.state.content.expression == "42"
    assert lookup_thread == [worker]
    assert publication == [threading.main_thread()]


def test_only_newest_preview_is_published():
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    request_existing(preview, ipc, ident=1)
    first = ipc.prepare_next()
    request_existing(preview, ipc, ident=2)
    request_existing(preview, ipc, ident=3)
    ipc.deliver(first)
    assert preview.state.content is None

    ipc.deliver(ipc.prepare_next())

    assert preview.state.content.expression == "3"


@pytest.mark.parametrize("retire", ["cancel", "close", "reset_capture"])
def test_retired_preview_cannot_publish_a_late_result(retire):
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    request_existing(preview, ipc)
    prepared = ipc.prepare_next()
    getattr(preview, retire)()

    ipc.deliver(prepared)

    assert preview.state.content is None


def test_replaced_source_refuses_ready_preview():
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    active = [True]
    captured = replace(source(), current=lambda: active[0])
    request_existing(preview, ipc, captured=captured)
    prepared = ipc.prepare_next()
    active[0] = False

    ipc.deliver(prepared)

    assert preview.state.content is None


def test_missing_note_degrades_to_toast():
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    toasts = []
    request_existing(
        preview,
        ipc,
        captured=source(note_info=lambda _id: None),
        toast=lambda *args: toasts.append(args),
    )

    ipc.deliver(ipc.prepare_next())

    assert preview.state.content is None
    assert toasts == [("already have 猫", "warn")]


@pytest.mark.parametrize("reject_inline", [False, True])
def test_refused_preparation_does_not_block_a_later_request(*, reject_inline):
    ipc = DeferredIPC()
    ipc.accept = False
    ipc.reject_inline = reject_inline
    preview = PreviewController(ipc)
    toasts = []
    request_existing(preview, ipc, toast=lambda *args: toasts.append(args))
    ipc.accept = True

    request_existing(preview, ipc, ident=2)
    ipc.deliver(ipc.prepare_next())

    assert toasts == [("already have 猫", "warn")]
    assert preview.state.content.expression == "2"


def test_sidebar_destination_retires_normally_after_it_arrives():
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    request_existing(preview, ipc, destination=PreviewDestination(10.0, 12.0, "previous"))

    assert preview.retains_cue("", None, None)
    assert preview.retains_cue("previous", 1.0, 2.0)
    assert preview.retains_cue("destination", 10.0, 12.0)
    assert not preview.retains_cue("previous", 1.0, 2.0)


class ReadAnki:
    def notes_info(self, _ids):
        return [{"fields": {"Expression": {"value": "猫"}}}]


def sidebar_session(tmp_path, monkeypatch):
    from saitenka_card import MineConfig
    from saitenka_subtitles import Cue, CueIndex
    from session_builder import build_session

    from saitenka.app.config import ReaderOptions
    from saitenka.app.features.mining import mined_store
    from saitenka.app.session.factory import SessionInfrastructure, SessionServices
    from saitenka.app.subtitle_render import NullRenderer

    monkeypatch.setattr(mined_store, "_DB_PATH_OVERRIDE", tmp_path / "mined.sqlite")
    video = str(tmp_path / "Show - 03.mkv")
    ipc = DeferredIPC()
    session = build_session(
        ipc,
        services=SessionServices(anki=ReadAnki(), mining=MineConfig()),
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    session.graph.playback.install_seed(
        {
            "path": video,
            "time-pos": 1.5,
            "sub-start": 1.0,
            "sub-end": 3.0,
            "sub-text": "前",
        }
    )
    session.graph.track_commands.navigation.current.sub_index = CueIndex(
        [
            Cue(1.0, 3.0, "前"),
            Cue(10.0, 12.0, "猫"),
            Cue(20.0, 22.0, "次"),
        ]
    )
    session.graph.cue.set_subtitle("前")
    session.graph.mining.store.record(
        note_id=42,
        video_path=video,
        cue_start=10.0,
        cue_end=12.0,
        expression="猫",
        reading="ねこ",
    )
    return session, ipc


def open_sidebar_preview(session):
    from saitenka.app.session.sidebar_coordination import open_mined

    open_mined(
        session.graph.sidebar.view(),
        session.graph.interaction.sidebar_actions(),
        session.graph.preview_commands,
        42,
    )


def observe_cue(session, ipc, *, text="猫", start=10.0, end=12.0):
    for name, value in (
        ("sub-text", ""),
        ("sub-start", start),
        ("sub-end", end),
        ("sub-text", text),
    ):
        ipc.set_prop(name, value)
        session.pump()


@pytest.mark.parametrize("complete_before_seek", [False, True])
def test_sidebar_preview_survives_its_separately_observed_seek(
    tmp_path, monkeypatch, *, complete_before_seek
):
    session, ipc = sidebar_session(tmp_path, monkeypatch)
    open_sidebar_preview(session)
    prepared = ipc.prepare_next()
    if complete_before_seek:
        ipc.deliver(prepared)

    observe_cue(session, ipc)
    if not complete_before_seek:
        ipc.deliver(prepared)

    assert session.graph.preview.state.content.expression == "猫"
    assert session.graph.preview.state.content.footer.endswith("00:10")
    assert ("set_property", "time-pos", 10.0) in ipc.commands


def test_sidebar_preview_does_not_require_a_subtitle_destination(tmp_path, monkeypatch):
    session, ipc = sidebar_session(tmp_path, monkeypatch)
    open_sidebar_preview(session)
    prepared = ipc.prepare_next()
    observe_cue(session, ipc, text="", start=None, end=None)

    ipc.deliver(prepared)

    assert session.graph.preview.state.content.expression == "猫"


@pytest.mark.parametrize("complete_before_seek", [False, True])
def test_failed_sidebar_preview_dismisses_the_previous_card(
    tmp_path, monkeypatch, *, complete_before_seek
):
    session, ipc = sidebar_session(tmp_path, monkeypatch)
    request_existing(session.graph.preview, ipc, ident=41)
    ipc.deliver(ipc.prepare_next())
    assert session.graph.preview.state.content.expression == "41"
    monkeypatch.setattr(ReadAnki, "notes_info", lambda *_args: [])
    open_sidebar_preview(session)
    prepared = ipc.prepare_next()
    if complete_before_seek:
        ipc.deliver(prepared)

    observe_cue(session, ipc)
    if not complete_before_seek:
        ipc.deliver(prepared)

    assert session.graph.preview.state.content is None


def test_sidebar_preview_is_cancelled_by_an_unrelated_settled_cue(tmp_path, monkeypatch):
    session, ipc = sidebar_session(tmp_path, monkeypatch)
    open_sidebar_preview(session)
    prepared = ipc.prepare_next()
    observe_cue(session, ipc, text="次", start=20.0, end=22.0)

    ipc.deliver(prepared)

    assert session.graph.preview.state.content is None


@pytest.mark.parametrize("retire", ["dismiss", "source", "profile", "connection", "close"])
def test_sidebar_preview_refuses_retired_session_context(tmp_path, monkeypatch, retire):
    session, ipc = sidebar_session(tmp_path, monkeypatch)
    open_sidebar_preview(session)
    prepared = ipc.prepare_next()
    if retire == "dismiss":
        session.graph.preview_commands.hide()
    elif retire == "source":
        ipc.set_prop("path", str(tmp_path / "Other - 02.mkv"))
        session.pump()
    elif retire == "profile":
        from saitenka.app.features.mining.mining_controller import MiningIdentity, MiningSpec

        session.graph.mining.select_mining_spec(MiningSpec.disabled(MiningIdentity("other", 1)))
    elif retire == "connection":
        session.graph.cue.retire("connection-lost")
    else:
        session.close()

    ipc.deliver(prepared)

    assert session.graph.preview.state.content is None


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_session_receives_shortcuts_while_preview_lookup_is_blocked():
    from util import await_ready, bare_gateway

    from saitenka.runtime import UserCommand

    ipc = FakeIPC()
    gateway = bare_gateway(ipc)
    preview = PreviewController(ipc)
    entered = threading.Event()
    release = threading.Event()
    received = []

    def note_info(ident):
        entered.set()
        assert release.wait(3)
        return {"fields": {"Expression": {"value": str(ident)}}}

    try:
        request_existing(preview, ipc, captured=source(note_info=note_info))
        assert entered.wait(1)

        ipc.emit({"event": "client-message", "args": ["saitenka-copy-line"]})
        ipc.receive_session(0.0, received.append)

        assert any(
            isinstance(event, UserCommand) and event.name == "saitenka-copy-line"
            for event in received
        )
        assert preview.state.content is None
        release.set()
        await_ready(
            lambda: preview.state.content is not None,
            "preview did not publish",
            pump=lambda: ipc.receive_session(0.0, received.append),
        )
        assert preview.state.content.expression == "42"
    finally:
        release.set()
        preview.close()
        gateway.close()


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_mined_preview_probes_audio_on_worker(tmp_path, monkeypatch):
    from PIL import Image
    from saitenka_tokenize.japanese import Token

    from saitenka.app import media

    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    jpg = tmp_path / "still.jpg"
    Image.new("RGB", (2, 2)).save(jpg)
    audio = tmp_path / "audio.m4a"
    preview.captured_image(jpg)
    preview.captured_audio(audio)
    probes = []
    prepared = []

    def probe(*_args, **_kwargs):
        probes.append(threading.current_thread())
        assert threading.current_thread() is not threading.main_thread()
        return SimpleNamespace(stdout="1.25")

    monkeypatch.setattr(media.subprocess, "run", probe)
    preview.present_mined(
        lambda: ports(preview, ipc),
        source,
        lambda *_args: None,
        SimpleNamespace(expression="猫", reading="ねこ", glosses=()),
        Token("猫", "猫", "ねこ", "名詞", 0, 1),
        "Show.mkv",
        enabled=True,
    )
    worker = threading.Thread(target=lambda: prepared.append(ipc.prepare_next()))
    worker.start()
    worker.join(1)
    assert not worker.is_alive()

    ipc.deliver(prepared[0])

    assert preview.state.content.audio_seconds == 1.25
    assert preview.state.content.image.size == (2, 2)
    assert preview.state.audio == audio
    assert probes == [worker]


def test_failed_preparation_toasts_without_showing_a_preview():
    ipc = DeferredIPC()
    preview = PreviewController(ipc)
    toasts = []
    request_existing(preview, ipc, toast=lambda *args: toasts.append(args))
    job = ipc.jobs.pop(0)

    job["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.INTERACTION,
            job["identity"],
            EffectOutcome.FAILED,
            error=EffectError.INTERNAL,
        )
    )

    assert preview.state.content is None
    assert toasts == [("already have 猫", "warn")]


def test_missing_runtime_warns_without_reading_anki():
    ipc = FakeIPC()
    preview = PreviewController(ipc)
    toasts = []
    lookups = []

    def note_info(ident):
        lookups.append(ident)

    request_existing(
        preview,
        ipc,
        captured=source(note_info=note_info),
        toast=lambda *args: toasts.append(args),
    )

    assert preview.state.content is None
    assert toasts == [("already have 猫", "warn")]
    assert lookups == []
