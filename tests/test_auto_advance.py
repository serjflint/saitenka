"""#100 auto-advance: the eof-reached edge hook + the reactive `file-loaded` re-slot (controller +
cli_run). The re-slot is driven by mpv's `file-loaded` so it follows a native autoload/playlist advance
AND our own eof loadfile through one setup path; auto-advance only decides whether we loadfile."""

from __future__ import annotations

import util

from saitenka.app import session_stats, subselect
from saitenka.app.controller import Reader
from saitenka.app.launch import run as cli_run
from saitenka.app.subtitle_render import NullRenderer


class FakeIPC(util.FakeIPC):
    """Models just enough of mpv's subtitle track model for the re-slot: `sub-add` appends an
    external track (deselected under the "auto" flag — selection stays with `sid`), `sub-remove`
    drops one, and `set_property sid` reselects, so a test can observe which srt the re-slot ends up
    selecting. Everything else, including the correlated-egress port, comes from the shared fake."""

    def __init__(self):
        super().__init__()
        self.pending_events: list[dict] = []

    def command(self, *args):
        reply = super().command(*args)
        op = args[0] if args else None
        if op == "sub-add":
            tl = self.props.setdefault("track-list", [])
            select = (args[2] if len(args) > 2 else "select") == "select"
            if select:
                for t in tl:
                    if t.get("type") == "sub":
                        t["selected"] = False
            tl.append(
                {
                    "type": "sub",
                    "id": max((t.get("id", 0) for t in tl), default=0) + 1,
                    "external": True,
                    "external-filename": args[1],
                    "lang": args[4] if len(args) > 4 else None,
                    "selected": select,
                }
            )
        elif op == "sub-remove":
            tl = self.props.get("track-list") or []
            self.props["track-list"] = [t for t in tl if t.get("id") != args[1]]
        elif op == "set_property" and len(args) > 2 and args[1] == "sid":
            for t in self.props.get("track-list") or []:
                if t.get("type") == "sub":
                    t["selected"] = t.get("id") == args[2]
            self.props["sid"] = args[2]
        return reply

    def drain_events(self, *_args, **_kwargs):
        evs, self.pending_events = self.pending_events, []
        return evs


def _observe_eof(reader, *, reached: bool) -> None:
    """Drive EOF the way mpv does. The advance is delta-driven now, so a test that poked a method
    and a clock would be exercising a path production no longer has."""
    reader._observe_property("eof-reached", reached)


def test_advance_fires_once_per_eof_edge():
    """One-shot per file with no latch to maintain: a delta exists only when the value changed, so
    mpv sitting paused at EOF republishing True is silence rather than a repeat advance."""
    reader = Reader(FakeIPC())
    calls: list[int] = []
    reader.advance_hook = lambda: bool(calls.append(1))

    _observe_eof(reader, reached=True)
    _observe_eof(reader, reached=True)  # still at EOF → must NOT re-fire
    assert calls == [1]

    _observe_eof(reader, reached=False)  # a fresh file cleared eof → re-arm
    _observe_eof(reader, reached=True)
    assert calls == [1, 1]


def test_advance_is_a_noop_without_a_hook():
    reader = Reader(FakeIPC())

    _observe_eof(reader, reached=True)  # attach/SyncPlay installs no hook → nothing, no crash


def test_reslot_to_current_rebinds_the_episode_without_reloading(tmp_path, monkeypatch):
    # The reactive re-slot re-indexes mpv's ALREADY-loaded file: no loadfile (mpv did it), but it
    # closes+reopens the stats row and rebinds the leak-free EpisodeContext so no prior state leaks.
    ipc = FakeIPC()
    reader = Reader(ipc)
    reader.jp_sid = 5  # dirty episode state that the re-slot must reset
    episode_before = reader.episode
    cur = tmp_path / "Show 04.mkv"
    ipc.props["path"] = str(cur)

    started: list[str] = []
    monkeypatch.setattr(session_stats, "finish", lambda _recorder, _analysis=None: None)
    monkeypatch.setattr(session_stats, "start", lambda r: started.append(str(r._prop("path"))))

    cli_run.reslot_to_current(reader, {}, cur, tmp_path, 0, cli_run.RunSubtitleOptions(slang="ja"))

    assert not any(c and c[0] == "loadfile" for c in ipc.commands)  # mpv already loaded it
    assert reader.episode is not episode_before  # a fresh EpisodeContext…
    assert reader.jp_sid is None  # …so prior-episode state cannot leak
    assert started == [str(cur)]  # a new stats row started against the current file


def test_reslot_drops_a_carried_over_external_and_tags_the_current_srt_japanese(
    tmp_path, monkeypatch
):
    # Live regression (#100): mpv re-applies the launch --sub-file (a PRIOR episode's srt) to every
    # playlist entry and auto-selects it, and re-adding the current srt UNTAGGED let _fill_untagged
    # latch onto that stale external — so ep03 showed ep2's lines as "unknown language 10/11" and
    # indexed ep2's cues. The re-slot must drop the stale external and select the jpn-tagged current srt.
    ipc = FakeIPC()
    reader = Reader(ipc)
    cur = tmp_path / "Show 03.mkv"
    ep3_srt = tmp_path / "Show 03.ja.srt"
    stale = tmp_path / "Show 02.ja.srt"  # the carried-over launch --sub-file mpv keeps re-adding
    for p, line in ((ep3_srt, "エピソード3"), (stale, "エピソード2")):
        p.write_text(f"1\n00:00:01,000 --> 00:00:02,000\n{line}\n", encoding="utf-8")
    ipc.props["path"] = str(cur)
    ipc.props["track-list"] = [
        {"type": "sub", "id": 1, "lang": "en", "selected": False},  # embedded CR English [default]
        {
            "type": "sub",
            "id": 10,
            "external": True,
            "external-filename": str(stale),
            "selected": True,
        },
    ]
    monkeypatch.setattr(session_stats, "finish", lambda _recorder, _analysis=None: None)
    monkeypatch.setattr(session_stats, "start", lambda _reader: None)

    cli_run.reslot_to_current(
        reader,
        {},
        cur,
        tmp_path,
        0,
        # sub_file stands in for the resolved current-episode subtitle
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp", sub_file=str(ep3_srt)),
    )

    assert ("sub-remove", 10) in ipc.commands  # the stale sibling srt is gone
    assert (
        "sub-add",
        str(ep3_srt),
        "auto",
        "",
        "ja",
    ) in ipc.commands  # current srt, tagged from slang
    selected = [t for t in ipc.props["track-list"] if t.get("selected")]
    assert len(selected) == 1 and selected[0]["external-filename"] == str(ep3_srt)
    assert reader.jp_sid == selected[0]["id"]  # …and it's the Japanese track the overlay reads


def test_on_file_loaded_reslots_once_per_distinct_file(tmp_path):
    # Regression: the overlay must follow mpv onto every newly loaded file (playlist/autoload/our own
    # loadfile), but must NOT re-slot the file it already set up — the initial load and a redundant
    # file-loaded for the same file are no-ops (they'd otherwise reset stats + re-add subs).
    ipc = FakeIPC()
    reader = Reader(ipc)
    seen = []
    reader.install_reslot_hook(seen.append, initial=tmp_path / "Show 01.mkv")

    ipc.props["path"] = str(tmp_path / "Show 01.mkv")
    reader._on_file_loaded()  # the initial file is already slotted → skip
    assert seen == []

    ipc.props["path"] = str(tmp_path / "Show 02.mkv")
    reader._on_file_loaded()  # a new file → re-slot
    reader._on_file_loaded()  # same file again → no re-slot
    assert seen == [tmp_path / "Show 02.mkv"]


def test_reconnect_reslots_file_changed_while_disconnected(tmp_path):
    ipc = FakeIPC()
    first = tmp_path / "Show 01.mkv"
    second = tmp_path / "Show 02.mkv"
    ipc.props.update({"path": str(first), "sub-text": "同じ字幕"})
    reader = Reader(ipc, prefetch=False, renderer=NullRenderer())
    reader.start_observing()
    reader.set_subtitle("同じ字幕")
    seen = []
    reader.install_reslot_hook(seen.append, initial=first)
    ipc.props["path"] = str(second)

    reader._on_ipc_reconnect()
    reader._on_property_change({"event": "property-change", "name": "path", "data": str(second)})
    reader._on_file_loaded()

    assert seen == [second]
    assert reader._cue_retired is True


def test_on_file_loaded_reslots_same_basename_from_a_different_parent(tmp_path):
    ipc = FakeIPC()
    reader = Reader(ipc)
    first = tmp_path / "season-1" / "Episode.mkv"
    second = tmp_path / "season-2" / "Episode.mkv"
    seen = []
    reader.install_reslot_hook(seen.append, initial=first)

    ipc.props["path"] = str(second)
    reader._on_file_loaded()

    assert seen == [second]


def test_on_file_loaded_resolves_relative_path_against_working_directory(tmp_path):
    ipc = FakeIPC()
    reader = Reader(ipc)
    seen = []
    reader.install_reslot_hook(seen.append, initial=tmp_path / "Show 01.mkv")

    ipc.props["working-directory"] = str(tmp_path)
    ipc.props["path"] = "Show 02.mkv"
    reader._on_file_loaded()

    assert seen == [tmp_path / "Show 02.mkv"]


def test_on_file_loaded_expands_tilde_before_reslot(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ipc = FakeIPC()
    reader = Reader(ipc)
    seen = []
    reader.install_reslot_hook(seen.append, initial=tmp_path / "Show 01.mkv")

    ipc.props["path"] = "~/Show 02.mkv"
    reader._on_file_loaded()

    assert seen == [tmp_path / "Show 02.mkv"]


def test_on_file_loaded_dispatched_from_drain_events(tmp_path):
    # Wiring guard: a `file-loaded` event pulled off the mpv stream must reach the re-slot hook.
    ipc = FakeIPC()
    reader = Reader(ipc)
    seen = []
    reader.install_reslot_hook(seen.append, initial=tmp_path / "Show 01.mkv")

    ipc.props["path"] = str(tmp_path / "Show 02.mkv")
    ipc.pending_events = [{"event": "file-loaded"}]
    reader._drain_events()
    assert seen == [tmp_path / "Show 02.mkv"]


def test_advance_defers_to_mpv_when_a_playlist_entry_is_next():
    # With an autoload/explicit playlist, mpv advances natively at EOF; we must NOT also loadfile (that
    # skips an episode). The reactive re-slot follows on file-loaded regardless.
    ipc = FakeIPC()
    reader = Reader(ipc)
    ipc.props["playlist-pos"] = 0
    ipc.props["playlist-count"] = 3

    assert cli_run._advance_at_eof(reader) is True
    assert not any(c and c[0] == "loadfile" for c in ipc.commands)


def test_advance_loadfiles_the_next_sibling_without_a_playlist(tmp_path):
    (tmp_path / "Show - 03.mkv").write_bytes(b"")
    (tmp_path / "Show - 04.mkv").write_bytes(b"")
    ipc = FakeIPC()
    reader = Reader(ipc)
    ipc.props["path"] = str(tmp_path / "Show - 03.mkv")
    ipc.props["playlist-pos"] = 0
    ipc.props["playlist-count"] = 1  # single file, no playlist to advance

    assert cli_run._advance_at_eof(reader) is True
    assert ("loadfile", str(tmp_path / "Show - 04.mkv")) in ipc.commands


def test_advance_holds_when_no_playlist_and_no_sibling(tmp_path):
    (tmp_path / "Show - 09.mkv").write_bytes(b"")  # last episode — no next sibling
    ipc = FakeIPC()
    reader = Reader(ipc)
    ipc.props["path"] = str(tmp_path / "Show - 09.mkv")
    ipc.props["playlist-count"] = 1

    assert cli_run._advance_at_eof(reader) is False  # hold the last frame (keep-open)
    assert not any(c and c[0] == "loadfile" for c in ipc.commands)


def test_watch_hooks_follow_playlists_even_with_auto_advance_off(tmp_path):
    # The regression the reactive design fixes: with --use-config, autoload advances the playlist and
    # the overlay must follow WITHOUT auto_advance — reslot_hook is installed, advance_hook is not.
    ipc = FakeIPC()
    reader = Reader(ipc)
    cli_run._install_watch_hooks(
        reader,
        {},
        tmp_path / "Show 01.mkv",
        tmp_path,
        0,
        cli_run.RunSubtitleOptions(slang="ja"),
        interactive=True,
        auto_advance=False,
    )
    assert reader.reslot_hook is not None  # follows file-loaded / playlists
    assert reader.advance_hook is None  # but never drives its own advance


def test_watch_hooks_not_installed_for_a_non_interactive_run(tmp_path):
    ipc = FakeIPC()
    reader = Reader(ipc)
    cli_run._install_watch_hooks(
        reader,
        {},
        tmp_path / "Show 01.mkv",
        tmp_path,
        0,
        cli_run.RunSubtitleOptions(slang="ja"),
        interactive=False,  # demo/screenshot — force-hover, not playback
        auto_advance=False,
    )
    assert reader.reslot_hook is None and reader.advance_hook is None


def test_prefetch_warms_the_next_sibling(tmp_path, monkeypatch):
    # #100: while ep03 plays, ep04's subs are fetched into cache so the next re-slot is synchronous
    # (no cold-start English gap). The fetch is a side-effecting cache write; we capture the episode.
    (tmp_path / "Show - 03.mkv").write_bytes(b"")
    (tmp_path / "Show - 04.mkv").write_bytes(b"")
    fetched: list[int | None] = []

    def fake_factory(providers, cfg, **_kw):
        assert providers == ("jimaku",)
        return lambda _video: lambda: fetched.append(cfg.episode)

    monkeypatch.setattr(subselect, "provider_fetch_factory", fake_factory)

    cli_run._prefetch_sibling_subs(
        {"jimaku": {"enabled": True, "fetch": True}},
        tmp_path / "Show - 03.mkv",
        enabled=True,
        jimaku_key=None,
        resync=False,
    )

    import time

    for _ in range(200):  # daemon thread — poll until it runs, bounded
        if fetched:
            break
        time.sleep(0.01)
    assert fetched == [4]  # warmed the NEXT episode, not the current one


def test_prefetch_is_a_noop_without_provider_or_sibling(tmp_path, monkeypatch):
    calls: list[object] = []
    monkeypatch.setattr(subselect, "provider_fetch_factory", lambda *_a, **_k: calls.append(1))

    # no provider configured → nothing to warm
    cli_run._prefetch_sibling_subs(
        {}, tmp_path / "Show - 03.mkv", enabled=True, jimaku_key=None, resync=False
    )
    # disabled (attach/SyncPlay) → never warms even with a provider
    cli_run._prefetch_sibling_subs(
        {"jimaku": {"fetch": True}},
        tmp_path / "Show - 03.mkv",
        enabled=False,
        jimaku_key=None,
        resync=False,
    )
    assert calls == []
