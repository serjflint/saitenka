from dataclasses import replace
from pathlib import Path

import util
from session_builder import build_session

from saitenka.app import subselect
from saitenka.app.commands import attach as attach_commands
from saitenka.app.episode_reslot import ReslotPorts
from saitenka.app.subtitle_modes import SubtitleStartup, SubtitleTracks
from saitenka.runtime.events import SubtitleTracksDiscovered


class IPC(util.FakeIPC):
    def __init__(self):
        super().__init__()
        self.props["path"] = "/videos/Show - 01.mkv"


class SessionController:
    def __init__(self):
        self.fetch = None
        self.retry_factory = None
        self.picker_lister = None

    def fetch_japanese_subs_async(self, fetch):
        self.fetch = fetch

    def configure_subtitle_retry(self, factory):
        self.retry_factory = factory

    def configure_sub_picker(self, lister):
        self.picker_lister = lister

    def rebuild_sub_index(self):
        self.rebuilt = True

    @property
    def reslot_ports(self):
        """The same shape `SessionController.reslot_ports` builds — the seam these hooks are driven through."""
        return ReslotPorts(
            ipc=None,
            finish_stats=lambda: None,
            start_stats=lambda: None,
            rebind_episode=lambda: None,
            rebuild_index=self.rebuild_sub_index,
            configure_mode=lambda *_a, **_kw: None,
            configure_retry=self.configure_subtitle_retry,
            configure_picker=self.configure_sub_picker,
            fetch_japanese=self.fetch_japanese_subs_async,
            start_prefetch=lambda: None,
            toast=lambda *_a, **_kw: None,
        )


def test_attach_defers_ordered_provider_chain_without_touching_playback(monkeypatch):
    reader, ipc = SessionController(), IPC()
    calls = []

    def fetch(video, providers, **kwargs):
        calls.append((video, providers, kwargs["tsukihime_config"]))
        return Path("episode.ja.ass"), "tsukihime: added"

    monkeypatch.setattr(subselect, "fetch_provider_path", fetch)

    attach_commands._finish_attach_subtitle_startup(
        reader.reslot_ports,
        ipc,
        None,
        subselect.ProviderConfig(
            enabled_providers=("jimaku", "tsukihime"),
            resync=False,
            tsukihime_config={"result_cap": 5},
        ),
        fetch_in_background=("jimaku", "tsukihime"),
    )

    assert reader.fetch is not None
    assert reader.retry_factory is not None
    assert ipc.commands == [("get_property", "path")]
    assert reader.fetch() == (Path("episode.ja.ass"), "tsukihime: added")
    assert calls == [("/videos/Show - 01.mkv", ("jimaku", "tsukihime"), {"result_cap": 5})]
    assert not any(command[0] in {"seek", "sub-seek"} for command in ipc.commands)
    assert not any(command[:2] == ("set_property", "pause") for command in ipc.commands)

    reader.retry_factory("/videos/Show - 02.mkv")()
    assert calls[-1][0] == "/videos/Show - 02.mkv"


def test_attach_configures_retry_even_when_startup_fetch_is_unneeded():
    reader, ipc = SessionController(), IPC()

    attach_commands._finish_attach_subtitle_startup(
        reader.reslot_ports,
        ipc,
        None,
        subselect.ProviderConfig(enabled_providers=("tsukihime",), resync=False),
        fetch_in_background=(),
    )

    assert reader.fetch is None
    assert reader.retry_factory is not None
    assert ipc.commands == []


class _TrackIPC(util.FakeIPC):
    """Models mpv's current path + a track-list carrying the previous episode's external sub."""

    def __init__(self):
        super().__init__()
        self.props |= {
            "path": "/videos/Show - 03.mkv",
            "track-list": [
                {"type": "sub", "id": 1, "lang": "en", "selected": False},
                {
                    "type": "sub",
                    "id": 10,
                    "external": True,
                    "external-filename": "/c/Show - 02.ja.srt",
                    "selected": True,
                },
            ],
        }

    def command(self, *args):
        if args and args[0] == "sub-remove":
            self.props["track-list"] = [
                t for t in self.props["track-list"] if t.get("id") != args[1]
            ]
        return super().command(*args)


def test_attach_reslot_resets_episode_drops_carryover_and_continues_japanese(monkeypatch):
    """On an ATTACH episode advance: close+reopen stats, reset subtitle navigation, drop
    the carried-over external, and — when the new file has no JP — defer a provider fetch so watching
    continues in Japanese. Reuses the run re-slot's contract (test_auto_advance) for attach."""
    from saitenka.app import session_stats

    ipc = _TrackIPC()
    reader = build_session(ipc)
    # stale prior-episode state the re-slot must clear
    reader.turn.track_commands.declare(SubtitleTracksDiscovered(99, None))
    episode_before = reader.turn.track_commands.navigation.current

    started: list[str] = []
    monkeypatch.setattr(session_stats, "finish", lambda _recorder, _analysis=None: None)
    monkeypatch.setattr(session_stats, "start", lambda *, path, **_kw: started.append(str(path())))
    monkeypatch.setattr(reader.turn.tooltip_controller, "start_prefetch", lambda: None)
    monkeypatch.setattr(reader.turn.notifications, "show", lambda *_a, **_k: None)
    # new episode carries English only → prepare_attach_startup defers a jimaku fetch
    monkeypatch.setattr(
        subselect,
        "prepare_attach_startup",
        lambda _ipc, _opts: (
            SubtitleStartup(SubtitleTracks(jp_sid=None, en_sid=1), "en"),
            "selected English fallback sid=1",
            ("jimaku",),
        ),
    )
    background: list = []
    ports = replace(reader.turn.reslot_ports, fetch_japanese=background.append)

    attach_commands._attach_reslot(
        ports,
        ipc,
        Path("/videos/Show - 03.mkv"),
        subselect.ProviderConfig(enabled_providers=("jimaku",), tsukihime_config={}, jimaku=True),
    )

    assert reader.turn.track_commands.navigation.current is not episode_before
    assert (
        reader.turn.track_commands.current().jp_sid is None
    )  # …so ep2's jp_sid=99 cannot leak into ep3
    assert ("sub-remove", 10) in ipc.commands  # carried-over ep2 external dropped
    assert started == ["/videos/Show - 03.mkv"]  # a new stats row on the current file
    assert (
        reader.turn.picker_controller.lister is not None
    )  # Ctrl+J picker rewired for the new episode
    assert len(background) == 1  # JP absent → provider fetch deferred (continue in Japanese)
