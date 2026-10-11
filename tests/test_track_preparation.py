"""Track preparation publishes only the current source without holding the session turn."""

import threading
from copy import deepcopy

import pytest
from util import FakeIPC, await_ready

from saitenka.app import track_preparation
from saitenka.app.config import ReaderOptions
from saitenka.app.session.factory import SessionInfrastructure
from saitenka.app.subtitle_render import NullRenderer


def track_session(make_session, source, *, media="/video.mkv", options=None):
    ipc = FakeIPC()
    ipc.props.update(
        {
            "path": media,
            "sid": 1,
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
        options=options or ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    return ipc, reader


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_startup_prepares_the_initial_track_after_observers_are_seeded(tmp_path, make_session):
    source = tmp_path / "initial.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\n本を読む\n", encoding="utf-8")
    _ipc, reader = track_session(make_session, source)
    reader.start()
    await_ready(
        lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
        "startup never installed the initial index",
        pump=reader.pump,
    )
    assert reader.graph.track_commands.navigation.current.sub_index.cues[0].text == "本を読む"


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_unusable_font_cache_preserves_navigation(tmp_path, monkeypatch, make_session):
    from saitenka.app import embedded_subs
    from saitenka.app.config import SubtitleGeometryOptions

    media = tmp_path / "video.mkv"
    media.touch()
    source = tmp_path / "valid.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\nValid subtitle\n", encoding="utf-8")
    blocked = tmp_path / "cache-file"
    blocked.touch()
    monkeypatch.setattr(embedded_subs, "container_fonts_cache_dir", lambda: blocked / "fonts")
    ipc, reader = track_session(
        make_session,
        source,
        media=str(media),
        options=ReaderOptions(
            subtitle_geometry=SubtitleGeometryOptions(native_visible=True), prefetch=False
        ),
    )
    ipc.props["options/embeddedfonts"] = True
    reader.graph.playback.install_seed(dict(ipc.props))
    reader.graph.cue.rebuild_sub_index()
    await_ready(
        lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
        "font failure suppressed indexing",
        pump=reader.pump,
    )
    assert reader.graph.track_commands.navigation.current.sub_index.cues[0].text == "Valid subtitle"


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("order", ["sid-first", "tracks-first", "new-entry"])
def test_manual_track_switch_prepares_the_observed_sid_in_either_order(
    tmp_path, make_session, order
):
    from saitenka.app import subtitle_modes

    paths = [tmp_path / "old.srt", tmp_path / "new.srt"]
    for path, text in zip(paths, ["古い", "New track"], strict=True):
        path.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n", encoding="utf-8")
    tracks = [
        {
            "id": i + 1,
            "type": "sub",
            "selected": i == 0,
            "lang": lang,
            "external": True,
            "external-filename": str(path),
        }
        for i, (path, lang) in enumerate(zip(paths, ["jpn", "eng"], strict=True))
    ]
    ipc = FakeIPC()
    ipc.props.update({"path": "/video.mkv", "sid": 1, "track-list": deepcopy(tracks)})
    reader = make_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.graph.playback.install_seed(
        {**ipc.props, "track-list": deepcopy(tracks[:1] if order == "new-entry" else tracks)}
    )
    updated = [{**t, "selected": t["id"] == 2} for t in tracks]
    observations = [("sid", 2), ("track-list", updated)]
    if order == "tracks-first":
        observations.reverse()
    for name, value in observations:
        ipc.props[name] = value
        reader.graph.playback.observe(name, value)
        reader.pump()
    await_ready(
        lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
        "new track was not prepared",
        pump=reader.pump,
    )
    assert reader.graph.track_commands.navigation.current.sub_index.cues[0].text == "New track"


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("failure", ["unreadable", "empty", "extraction"])
@pytest.mark.parametrize("lang", ["eng", None])
def test_failed_preparation_still_adopts_the_current_track_role(
    tmp_path, monkeypatch, make_session, failure, lang
):
    from saitenka_tokenize.languages import SECOND_LANG

    from saitenka.app import embedded_subs, subtitle_modes

    path = tmp_path / "unindexed.srt"
    if failure == "empty":
        path.write_text("", encoding="utf-8")
    track = {
        "id": 2,
        "type": "sub",
        "selected": True,
        "lang": lang,
        "external": True,
        "external-filename": str(path),
    }
    if failure == "extraction":
        track = {
            "id": 2,
            "type": "sub",
            "selected": True,
            "lang": lang,
            "ff-index": 1,
            "codec": "ass",
        }
        monkeypatch.setattr(embedded_subs, "extract_embedded_track", lambda *_a, **_kw: False)
        monkeypatch.setattr(embedded_subs, "embedded_subs_cache_dir", lambda: tmp_path / "cache")
    ipc = FakeIPC()
    ipc.props.update(
        {
            "path": "/video.mkv",
            "sid": 1,
            "track-list": [{"id": 1, "type": "sub", "lang": "jpn", "selected": True}],
        }
    )
    reader = make_session(
        ipc,
        options=ReaderOptions().with_overrides(prefetch=False),
        infrastructure=SessionInfrastructure(renderer=NullRenderer()),
    )
    reader.graph.cue.configure_subtitle_mode(subtitle_modes.select_initial(ipc))
    reader.graph.playback.install_seed({**ipc.props, "sub-text": "English on screen"})
    ipc.props.update({"sid": 2, "track-list": [track], "sub-text": "English on screen"})
    reader.graph.playback.observe("track-list", [track])
    reader.graph.playback.observe("sid", 2)
    await_ready(
        lambda: reader.graph.track_commands.current().en_sid == 2,
        "failed indexing suppressed role adoption",
        pump=reader.pump,
    )
    assert reader.graph.track_commands.current().language == SECOND_LANG
    assert reader.graph.track_commands.navigation.current.sub_index is None


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_cancelling_preparation_interrupts_extraction_and_removes_staging(tmp_path, monkeypatch):
    import sys
    from concurrent.futures import ThreadPoolExecutor

    import psutil

    from saitenka.app import embedded_subs, procutil

    ready = tmp_path / "ready"
    script = (
        "import os,signal,time\nfrom pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"Path({str(ready)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n"
    )
    run = procutil.run_cancellable

    def extract(_command, **kwargs):
        return run([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(procutil, "run_cancellable", extract)
    cache = tmp_path / "cache"
    monkeypatch.setattr("saitenka.mpvio.discover.find_tool", lambda name: name)
    monkeypatch.setattr(embedded_subs, "embedded_subs_cache_dir", lambda: cache)
    request = track_preparation.TrackRequest(
        FakeIPC(),
        {
            "path": "/video.mkv",
            "track-list": [
                {"id": 1, "type": "sub", "selected": True, "ff-index": 0, "codec": "ass"}
            ],
        },
        0,
        threading.Event(),
    )
    stopped = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(track_preparation.prepare_track, request, stopped)
        try:
            await_ready(ready.exists, "extractor did not start")
            pid = int(ready.read_text(encoding="utf-8"))
            stopped.set()
            assert work.result(timeout=2) is None
            assert not psutil.pid_exists(pid)
            assert list(cache.iterdir()) == []
        finally:
            stopped.set()
            if ready.exists():
                try:
                    psutil.Process(int(ready.read_text(encoding="utf-8"))).kill()
                except psutil.NoSuchProcess:
                    pass


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("retire", ["profile", "role"])
def test_profile_annotation_retirement_does_not_cancel_track_preparation(
    tmp_path, monkeypatch, make_session, retire
):
    path = tmp_path / "track.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:02,000\n本を読む\n", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    load = track_preparation.load_index

    def held(path, **kwargs):
        entered.set()
        assert release.wait(5)
        return load(path, **kwargs)

    monkeypatch.setattr(track_preparation, "load_index", held)
    ipc, reader = track_session(make_session, path)
    reader.graph.playback.install_seed(dict(ipc.props))
    try:
        reader.graph.cue.rebuild_sub_index()
        assert entered.wait(2)
        reader.graph.cue.retire(retire)
        ipc.set_prop("pause", value=True)
        reader.pump()
        assert reader.graph.playback.value("pause") is True
        assert reader.graph.track_commands.navigation.current.sub_index is None
        release.set()
        await_ready(
            lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
            "preparation never published",
            pump=reader.pump,
        )
        assert reader.graph.track_commands.navigation.current.sub_index.cues[0].text == "本を読む"
    finally:
        release.set()


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_same_sid_replacement_cannot_install_the_previous_document(
    tmp_path, monkeypatch, make_session
):
    paths = [tmp_path / name for name in ("old.srt", "superseded.srt", "new.srt")]
    for path, text in zip(paths, ["古い", "Superseded", "新しい"], strict=True):
        path.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{text}\n", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    load = track_preparation.load_index

    def held(path, **kwargs):
        if path == paths[0]:
            entered.set()
            release.wait(5)
        return load(path, **kwargs)

    monkeypatch.setattr(track_preparation, "load_index", held)
    ipc, reader = track_session(make_session, paths[0])
    track = ipc.props["track-list"][0]
    reader.graph.playback.install_seed(dict(ipc.props))
    try:
        reader.graph.cue.rebuild_sub_index()
        assert entered.wait(2)
        for path in paths[1:]:
            replacement = [{**track, "external-filename": str(path)}]
            ipc.props["track-list"] = replacement
            reader.graph.playback.observe("track-list", replacement)
            reader.graph.cue.rebuild_sub_index()
        release.set()
        await_ready(
            lambda: reader.graph.track_commands.navigation.current.sub_index is not None,
            "replacement never published",
            pump=reader.pump,
        )
        assert reader.graph.track_commands.navigation.current.sub_index.cues[0].text == "新しい"
    finally:
        release.set()


@pytest.mark.parametrize(
    "changed", [None, "artifact", "font", "epoch", "media", "selection", "retired"]
)
def test_preparation_adoption_requires_the_same_source_and_environment(monkeypatch, changed):
    from pathlib import Path

    from saitenka_subtitles import Cue, CueIndex

    from saitenka.app import subtitle_fonts
    from saitenka.runtime import EffectFinished, EffectId, EffectOutcome, Owner

    accepted, adopted = [], []

    def submit(**kwargs):
        accepted.append(kwargs)
        return True

    monkeypatch.setattr(track_preparation, "configure_lane", lambda *_args: submit)
    track = {
        "id": 1,
        "type": "sub",
        "selected": True,
        "external": True,
        "external-filename": "/old.ass",
    }
    current = {
        "_epoch": 0,
        "path": "/video.mkv",
        "sid": 1,
        "track-list": [track],
        "options/sub-font": "Arial",
    }
    settings = {name: current.get(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS}
    prepared = track_preparation.PreparedTrack(
        Path("/old.ass"),
        "/video.mkv",
        1,
        "ass",
        CueIndex([Cue(1, 2, "本")]),
        track_preparation.PreparedSource(b"", frozenset(), {}),
        subtitle_fonts.FontEnvironment(options=subtitle_fonts.option_snapshot(settings)),
        track,
    )
    controller = track_preparation.TrackPreparation(
        FakeIPC(), lambda: dict(current), adopted.append, lambda _properties: None
    )
    controller.request()
    updates = {
        None: {},
        "artifact": {"track-list": [{**track, "external-filename": "/new.ass"}]},
        "font": {"options/sub-font": "Other"},
        "epoch": {"_epoch": 1},
        "media": {"path": "/new.mkv"},
        "selection": {"sid": 2},
        "retired": {},
    }
    current.update(updates[changed])
    if changed == "retired":
        controller.cancel()

    accepted[0]["on_finished"](
        EffectFinished(
            EffectId(1),
            Owner.SUBTITLE,
            accepted[0]["identity"],
            EffectOutcome.SUCCEEDED,
            result=prepared,
        )
    )

    assert adopted == ([prepared] if changed is None else [])
