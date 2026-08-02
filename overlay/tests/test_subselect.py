"""attach/plugin-mode subtitle selection: pick the JP track over the user's English-first mpv, or
fetch jimaku when the file has no JP subs. A FakeIPC records commands and serves track-list/path."""

from __future__ import annotations

from overlay.app import subselect


class FakeIPC:
    def __init__(self, tracks=None, path=None):
        self._tracks = tracks or []
        self._path = path
        self.calls: list[tuple] = []

    def command(self, *args):
        self.calls.append(args)
        if args[:2] == ("get_property", "track-list"):
            return {"data": self._tracks}
        if args[:2] == ("get_property", "path"):
            return {"data": self._path}
        return {"data": None}

    def sets(self, prop):
        return [a[2] for a in self.calls if a[:2] == ("set_property", prop)]


JP = {"id": 2, "type": "sub", "lang": "jpn"}
EN = {"id": 1, "type": "sub", "lang": "eng"}


def test_select_prefers_japanese_over_english():
    ipc = FakeIPC(tracks=[EN, JP])
    sid = subselect.select_sub_track(ipc, "ja,jpn,jp")
    assert sid == 2
    assert ipc.sets("sid") == [2]


def test_select_returns_none_when_no_sub_tracks():
    ipc = FakeIPC(tracks=[{"id": 1, "type": "audio", "lang": "jpn"}])
    assert subselect.select_sub_track(ipc, "ja,jpn") is None
    assert ipc.sets("sid") == []


def test_lang_matches_two_and_three_letter_and_name():
    assert subselect._lang_matches("jpn", ["jpn"])
    assert subselect._lang_matches("ja", ["ja"])
    assert subselect._lang_matches("Japanese", ["ja"])
    assert not subselect._lang_matches("eng", ["ja", "jpn", "jp"])


def test_ensure_selects_jp_and_hides_native_subs():
    ipc = FakeIPC(tracks=[EN, JP])
    msg = subselect.ensure_jp_subs(ipc, slang="ja,jpn,jp")
    assert "sid=2" in msg
    assert ipc.sets("sub-visibility") == [False]  # overlay draws its own


def test_ensure_no_jp_without_jimaku_reports_gap():
    ipc = FakeIPC(tracks=[EN])
    msg = subselect.ensure_jp_subs(ipc, slang="ja,jpn,jp")
    assert "no Japanese subtitle track" in msg
    assert ipc.sets("sub-visibility") == []  # left mpv alone


def test_attach_starts_with_english_and_defers_enabled_jimaku():
    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")
    startup, status, fetch_in_background = subselect.prepare_attach_startup(ipc, jimaku=True)
    assert startup.active == "en" and startup.tracks.en_sid == 1
    assert "English fallback" in status
    assert fetch_in_background == ("jimaku",)
    assert ipc.sets("sid") == [1]


def test_attach_does_not_fetch_when_japanese_is_already_present():
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Has Japanese - 01.mkv")
    startup, _status, fetch_in_background = subselect.prepare_attach_startup(ipc, jimaku=True)
    assert startup.active == "jp"
    assert fetch_in_background == ()
    assert ipc.sets("sid") == [2]


def test_attach_orders_enabled_jimaku_before_tsukihime():
    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")

    startup, _status, providers = subselect.prepare_attach_startup(ipc, jimaku=True, tsukihime=True)

    assert startup.active == "en"
    assert providers == ("jimaku", "tsukihime")


def test_disabled_tsukihime_is_absent_from_provider_chain():
    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")

    _startup, _status, providers = subselect.prepare_attach_startup(ipc, tsukihime=False)

    assert providers == ()


def test_tsukihime_provider_error_returns_soft_status(tmp_path, monkeypatch):
    import overlay.app.jimaku as jm
    import overlay.app.tsukihime as th

    monkeypatch.setattr(jm, "parse_filename", lambda _path: ("Show", 1))

    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, *_args):
            raise th.TsukiHimeError("malformed detail")

    monkeypatch.setattr(th, "TsukiHimeClient", FailingClient)

    path, status = subselect.fetch_tsukihime_path(str(tmp_path / "Show - 01.mkv"), resync=False)

    assert path is None
    assert status == "tsukihime failed: malformed detail"


def test_tsukihime_fetch_reuses_shared_cache(tmp_path, monkeypatch):
    import overlay.app.subtitle_cache as cache
    import overlay.app.tsukihime as th

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")
    cached = cache.store_subs(video, "Show", 1, downloaded, resync=True)
    monkeypatch.setattr(
        th,
        "TsukiHimeClient",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("network fetch")),
    )

    path, status = subselect.fetch_tsukihime_path(str(video), resync=True)

    assert path == cached
    assert status == f"subtitle cache: using {cached.name} for 'Show' ep 1"


def test_tsukihime_fetch_stores_finished_subtitle(tmp_path, monkeypatch):
    import overlay.app.subtitle_cache as cache
    import overlay.app.tsukihime as th

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, _title, _episode, _destination):
            return downloaded

    monkeypatch.setattr(th, "TsukiHimeClient", FakeClient)

    path, _status = subselect.fetch_tsukihime_path(str(video), resync=False)

    assert path == cache.cached_subs(video, "Show", 1, resync=False)
    assert path is not None and path.read_text(encoding="utf-8") == "Japanese"


def test_ensure_sub_file_is_added_and_selected(tmp_path):
    sub = tmp_path / "ep.ja.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n", encoding="utf-8")
    ipc = FakeIPC(tracks=[EN])
    msg = subselect.ensure_jp_subs(ipc, sub_file=str(sub))
    assert "ep.ja.srt" in msg
    assert ("sub-add", str(sub), "select") in ipc.calls


def test_ensure_jimaku_fetches_when_no_jp_track(tmp_path, monkeypatch):
    fetched = tmp_path / "fetched.ja.srt"
    fetched.write_text("x")
    ipc = FakeIPC(tracks=[EN], path="/v/Nippon Sangoku - 09.mkv")

    monkeypatch.setattr(subselect, "_add_and_select", lambda ipc, p: ipc.command("sub-add", str(p)))

    class FakeClient:
        def __init__(self, key=None):
            pass

        def fetch(self, _title, _ep, _dest):
            return fetched

    import overlay.app.jimaku as jm

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)
    monkeypatch.setattr(jm, "parse_filename", lambda _p: ("Nippon Sangoku", 9))
    # resync off so we don't shell out
    msg = subselect.ensure_jp_subs(ipc, jimaku=True, resync=False)
    assert "jimaku: added fetched.ja.srt" in msg and "ep 9" in msg
    assert ("sub-add", str(fetched)) in ipc.calls


def test_background_jimaku_fetch_reuses_persistent_cache(tmp_path, monkeypatch):
    import overlay.app.jimaku as jm

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")
    cached = jm.store_subs(video, "Show", 1, downloaded)
    monkeypatch.setattr(
        jm,
        "JimakuClient",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network fetch")),
    )

    path, status = subselect.fetch_jimaku_path(str(video), resync=True)

    assert path == cached
    assert status == f"subtitle cache: using {cached.name} for 'Show' ep 1"


def test_background_jimaku_fetch_stores_finished_subtitle(tmp_path, monkeypatch):
    import overlay.app.jimaku as jm

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")

    class FakeClient:
        def __init__(self, _key=None):
            pass

        def fetch(self, _title, _episode, _dest):
            return downloaded

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)

    path, _status = subselect.fetch_jimaku_path(str(video), resync=False)

    assert path == jm.cached_subs(video, "Show", 1, resync=False)
    assert path is not None and path.read_text(encoding="utf-8") == "Japanese"


def _stub_jimaku(monkeypatch, tmp_path, *, ok=True):
    fetched = tmp_path / "fetched.ja.srt"
    fetched.write_text("x")
    monkeypatch.setattr(subselect, "_add_and_select", lambda ipc, p: ipc.command("sub-add", str(p)))
    import overlay.app.jimaku as jm

    class FakeClient:
        def __init__(self, key=None):
            pass

        def fetch(self, _title, _ep, _dest):
            if not ok:
                raise jm.JimakuError("not found")
            return fetched

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)
    monkeypatch.setattr(jm, "parse_filename", lambda _p: ("Nippon Sangoku", 9))
    return fetched


def test_jimaku_force_prefers_jimaku_over_embedded_jp_track(tmp_path, monkeypatch):
    fetched = _stub_jimaku(monkeypatch, tmp_path)
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Nippon Sangoku - 09.mkv")
    msg = subselect.ensure_jp_subs(ipc, jimaku=True, jimaku_force=True, resync=False)
    assert "jimaku: added fetched.ja.srt" in msg
    assert ("sub-add", str(fetched)) in ipc.calls
    assert ipc.sets("sid") == []  # embedded JP track was NOT selected — jimaku won


def test_jimaku_force_falls_back_to_embedded_on_fetch_failure(tmp_path, monkeypatch):
    _stub_jimaku(monkeypatch, tmp_path, ok=False)
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Nippon Sangoku - 09.mkv")
    msg = subselect.ensure_jp_subs(ipc, jimaku=True, jimaku_force=True, resync=False)
    assert "sid=2" in msg  # jimaku failed → embedded JP track selected as fallback
    assert ipc.sets("sid") == [2]


def test_provider_path_runs_configured_order_and_returns_first_success(tmp_path, monkeypatch):
    calls = []
    hit = tmp_path / "th.srt"

    def fake_jimaku(_video, **_kwargs):
        calls.append("jimaku")
        return None, "jimaku: none"

    def fake_tsukihime(_video, **_kwargs):
        calls.append("tsukihime")
        return hit, "tsukihime: added th.srt"

    monkeypatch.setattr(subselect, "fetch_jimaku_path", fake_jimaku)
    monkeypatch.setattr(subselect, "fetch_tsukihime_path", fake_tsukihime)

    path, status = subselect.fetch_provider_path("/v/Show - 01.mkv", ("jimaku", "tsukihime"))

    assert path == hit
    assert status == "tsukihime: added th.srt"
    assert calls == ["jimaku", "tsukihime"]  # jimaku tried first, tsukihime won


def test_provider_path_skips_unknown_provider_names(tmp_path, monkeypatch):
    hit = tmp_path / "jm.srt"
    calls = []

    def fake_jimaku(_video, **_kwargs):
        calls.append("jimaku")
        return hit, "jimaku: ok"

    def fail_tsukihime(*_args, **_kwargs):
        raise AssertionError("tsukihime should not run")

    monkeypatch.setattr(subselect, "fetch_jimaku_path", fake_jimaku)
    monkeypatch.setattr(subselect, "fetch_tsukihime_path", fail_tsukihime)

    path, status = subselect.fetch_provider_path("/v/Show - 01.mkv", ("bogus", "jimaku"))

    assert path == hit
    assert status == "jimaku: ok"
    assert calls == ["jimaku"]  # the unknown name produced no attempt


def test_provider_path_with_no_providers_reports_none(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("no provider should run")

    monkeypatch.setattr(subselect, "fetch_jimaku_path", fail)
    monkeypatch.setattr(subselect, "fetch_tsukihime_path", fail)

    path, status = subselect.fetch_provider_path("/v/Show - 01.mkv", ())

    assert path is None
    assert status == "no Japanese subtitle providers enabled"
