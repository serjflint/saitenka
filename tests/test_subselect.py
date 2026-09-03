"""attach/plugin-mode subtitle selection: pick the JP track over the user's English-first mpv, or
fetch jimaku when the file has no JP subs.

The fake is the shared gateway-wired one, so selection can be exercised through the runtime command
path rather than a bare ``.command()`` recorder. ``calls``/``sets`` keep the assertions the file was
written with.
"""

from __future__ import annotations

import util

from saitenka.app import subselect


class FakeIPC(util.FakeIPC):
    def __init__(self, tracks=None, path=None):
        super().__init__()
        self.props["track-list"] = tracks or []
        self.props["path"] = path
        util.bare_gateway(self)

    def command(self, *args):
        reply = super().command(*args)
        # mpv publishes an added track on `track-list`, and the selection that follows reads it
        # back. A fake that only records the call cannot tell "added then selected" from "added and
        # then ignored" — which is the whole of the startup ordering these tests are about.
        if args and args[0] == "sub-add":
            self.props["track-list"] = [
                *self.props["track-list"],
                {
                    "id": 9,
                    "type": "sub",
                    "lang": args[4] if len(args) > 4 else None,
                    "external": True,
                    "external-filename": args[1],
                },
            ]
        return reply

    @property
    def calls(self) -> list[tuple]:
        return self.commands

    def sets(self, prop):
        return [a[2] for a in self.commands if a[:2] == ("set_property", prop)]


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
    msg = subselect.ensure_jp_subs(ipc, subselect.AttachSubtitleOptions(slang="ja,jpn,jp"))
    assert "sid=2" in msg
    assert ipc.sets("sub-visibility") == []  # ownership is decided after SessionController exists


def test_ensure_no_jp_without_jimaku_reports_gap():
    ipc = FakeIPC(tracks=[EN])
    msg = subselect.ensure_jp_subs(ipc, subselect.AttachSubtitleOptions(slang="ja,jpn,jp"))
    assert "no Japanese subtitle track" in msg
    assert ipc.sets("sub-visibility") == []  # left mpv alone


def test_attach_starts_with_english_and_defers_enabled_jimaku():
    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")
    startup, status, fetch_in_background = subselect.prepare_attach_startup(
        ipc, subselect.AttachSubtitleOptions(jimaku=True)
    )
    assert startup.active == "en" and startup.tracks.en_sid == 1
    assert "English fallback" in status
    assert fetch_in_background == ("jimaku",)
    assert ipc.sets("sid") == [1]


def test_attach_does_not_fetch_when_japanese_is_already_present():
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Has Japanese - 01.mkv")
    startup, _status, fetch_in_background = subselect.prepare_attach_startup(
        ipc, subselect.AttachSubtitleOptions(jimaku=True)
    )
    assert startup.active == "jp"
    assert fetch_in_background == ()
    assert ipc.sets("sid") == [2]


def test_attach_classifies_the_configured_second_language_track():
    tracks = [
        {"id": 6, "type": "sub", "lang": "fra"},
        {"id": 7, "type": "sub", "lang": "eng"},
        {"id": 8, "type": "sub", "lang": "deu"},
    ]
    ipc = FakeIPC(tracks=tracks, path="/v/Multilingual - 01.mkv")

    startup, _status, _providers = subselect.prepare_attach_startup(
        ipc,
        subselect.AttachSubtitleOptions(slang="fr", language="fr", second_language="de"),
    )

    assert (startup.tracks.jp_sid, startup.tracks.en_sid, ipc.sets("sid")) == (6, 8, [6])


def test_attach_orders_enabled_jimaku_before_tsukihime():
    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")

    startup, _status, providers = subselect.prepare_attach_startup(
        ipc, subselect.AttachSubtitleOptions(jimaku=True, tsukihime=True)
    )

    assert startup.active == "en"
    assert providers == ("jimaku", "tsukihime")


def test_disabled_tsukihime_is_absent_from_provider_chain():
    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")

    _startup, _status, providers = subselect.prepare_attach_startup(
        ipc, subselect.AttachSubtitleOptions(tsukihime=False)
    )

    assert providers == ()


def test_attach_startup_provider_list_matches_the_registry_enablement_for_ja():
    """Single source of truth: the deferred initial-fetch list from prepare_attach_startup must equal
    the registry/language-gated enablement the retry+picker use (cli.py), so the two can't diverge
    under a non-jp profile. jimaku_force is fetched ahead, so it's excluded from the deferred list."""
    from saitenka_tokenize.languages import MAIN_LANG

    from saitenka.app.subtitle_providers import enabled_providers_for

    ipc = FakeIPC(tracks=[EN], path="/v/English Only - 01.mkv")
    _startup, _status, providers = subselect.prepare_attach_startup(
        ipc, subselect.AttachSubtitleOptions(jimaku=True, tsukihime=True)
    )

    assert providers == enabled_providers_for(MAIN_LANG, (("jimaku", True), ("tsukihime", True)))


def test_tsukihime_provider_error_returns_soft_status(tmp_path, monkeypatch):
    import saitenka.app.jimaku as jm
    import saitenka.app.tsukihime as th

    monkeypatch.setattr(jm, "parse_filename", lambda _path: ("Show", 1))

    class FailingClient:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, *_args, **_kwargs):
            raise th.TsukiHimeError("malformed detail")

    monkeypatch.setattr(th, "TsukiHimeClient", FailingClient)

    path, status = subselect.fetch_tsukihime_path(str(tmp_path / "Show - 01.mkv"), resync=False)

    assert path is None
    assert status == "tsukihime failed: malformed detail"


def test_tsukihime_fetch_reuses_shared_cache(tmp_path, monkeypatch):
    import saitenka.app.subtitle_cache as cache
    import saitenka.app.tsukihime as th

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
    import saitenka.app.subtitle_cache as cache
    import saitenka.app.tsukihime as th

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def fetch(self, _title, _episode, _destination, **_kwargs):
            return downloaded

    monkeypatch.setattr(th, "TsukiHimeClient", FakeClient)

    path, _status = subselect.fetch_tsukihime_path(str(video), resync=False)

    assert path == cache.cached_subs(video, "Show", 1, resync=False)
    assert path is not None and path.read_text(encoding="utf-8") == "Japanese"


def test_ensure_sub_file_is_added_and_selected(tmp_path):
    sub = tmp_path / "ep.ja.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:02,000\nこんにちは\n", encoding="utf-8")
    ipc = FakeIPC(tracks=[EN])
    msg = subselect.ensure_jp_subs(ipc, subselect.AttachSubtitleOptions(sub_file=str(sub)))
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

        def fetch(self, _title, _ep, _dest, **_kwargs):
            return fetched

    import saitenka.app.jimaku as jm

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)
    monkeypatch.setattr(jm, "parse_filename", lambda _p: ("Nippon Sangoku", 9))
    # resync off so we don't shell out
    msg = subselect.ensure_jp_subs(ipc, subselect.AttachSubtitleOptions(jimaku=True, resync=False))
    assert "jimaku: added fetched.ja.srt" in msg and "ep 9" in msg
    assert ("sub-add", str(fetched)) in ipc.calls


def test_background_jimaku_fetch_reuses_persistent_cache(tmp_path, monkeypatch):
    import saitenka.app.jimaku as jm

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


def test_force_bypasses_the_persistent_cache_and_refetches(tmp_path, monkeypatch):
    # The retry keybind passes force=True so a stale/mistimed cached srt (e.g. an ffsubsync no-op that
    # cached raw under the synced key) is re-fetched + re-synced, not silently reused.
    import saitenka.app.jimaku as jm

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    stale = tmp_path / "stale.srt"
    stale.write_text("STALE\n", encoding="utf-8")
    jm.store_subs(video, "Show", 1, stale, resync=False)  # a pre-existing (bad) cache entry

    fresh = tmp_path / "fresh.srt"
    fresh.write_text("FRESH\n", encoding="utf-8")

    class FakeClient:
        def __init__(self, _key=None):
            pass

        def fetch(self, _title, _episode, _dest, **_kwargs):
            return fresh

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)

    path, _status = subselect.fetch_jimaku_path(str(video), resync=False, force=True)

    assert path is not None and path.read_text(encoding="utf-8") == "FRESH\n"  # cache bypassed
    # and the fresh sub overwrote the cache, so later (non-force) reads see it too
    assert jm.cached_subs(video, "Show", 1, resync=False).read_text(encoding="utf-8") == "FRESH\n"


def test_background_jimaku_fetch_stores_finished_subtitle(tmp_path, monkeypatch):
    import saitenka.app.jimaku as jm

    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")

    class FakeClient:
        def __init__(self, _key=None):
            pass

        def fetch(self, _title, _episode, _dest, **_kwargs):
            return downloaded

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)

    path, _status = subselect.fetch_jimaku_path(str(video), resync=False)

    assert path == jm.cached_subs(video, "Show", 1, resync=False)
    assert path is not None and path.read_text(encoding="utf-8") == "Japanese"


def _stub_jimaku(monkeypatch, tmp_path, *, ok=True):
    fetched = tmp_path / "fetched.ja.srt"
    fetched.write_text("x")
    monkeypatch.setattr(subselect, "_add_and_select", lambda ipc, p: ipc.command("sub-add", str(p)))
    import saitenka.app.jimaku as jm

    class FakeClient:
        def __init__(self, key=None):
            pass

        def fetch(self, _title, _ep, _dest, **_kwargs):
            if not ok:
                raise jm.JimakuError("not found")
            return fetched

    monkeypatch.setattr(jm, "JimakuClient", FakeClient)
    monkeypatch.setattr(jm, "parse_filename", lambda _p: ("Nippon Sangoku", 9))
    return fetched


def test_jimaku_force_prefers_jimaku_over_embedded_jp_track(tmp_path, monkeypatch):
    fetched = _stub_jimaku(monkeypatch, tmp_path)
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Nippon Sangoku - 09.mkv")
    msg = subselect.ensure_jp_subs(
        ipc, subselect.AttachSubtitleOptions(jimaku=True, jimaku_force=True, resync=False)
    )
    assert "jimaku: added fetched.ja.srt" in msg
    assert ("sub-add", str(fetched)) in ipc.calls
    assert ipc.sets("sid") == []  # embedded JP track was NOT selected — jimaku won


def test_jimaku_force_falls_back_to_embedded_on_fetch_failure(tmp_path, monkeypatch):
    _stub_jimaku(monkeypatch, tmp_path, ok=False)
    ipc = FakeIPC(tracks=[EN, JP], path="/v/Nippon Sangoku - 09.mkv")
    msg = subselect.ensure_jp_subs(
        ipc, subselect.AttachSubtitleOptions(jimaku=True, jimaku_force=True, resync=False)
    )
    assert "sid=2" in msg  # jimaku failed → embedded JP track selected as fallback
    assert ipc.sets("sid") == [2]


def test_jimaku_force_cannot_bypass_profile_language_eligibility(monkeypatch):
    monkeypatch.setattr(
        subselect,
        "fetch_jimaku",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("ineligible provider called")),
    )
    de = {"id": 7, "type": "sub", "lang": "de"}
    ipc = FakeIPC(tracks=[de], path="/v/French - 01.mkv")

    startup, _status, providers = subselect.prepare_attach_startup(
        ipc,
        subselect.AttachSubtitleOptions(
            slang="fr",
            jimaku=True,
            jimaku_force=True,
            language="fr",
            second_language="de",
        ),
    )

    assert (startup.active, startup.tracks.en_sid) == ("en", 7)
    assert providers == ()


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


# --- Window 1: provider-agnostic candidate aggregation --------------------------------------------


def test_list_candidates_aggregates_providers_in_order(monkeypatch):
    """list_candidates concatenates each enabled provider's candidates, in the given provider order."""

    def _cand(provider, name, *, match):
        return subselect.SubtitleCandidate(
            provider=provider, name=name, size=0, match=match, download=lambda: (None, "")
        )

    def jimaku(*_args):
        return [_cand("jimaku", "a.srt", match=True)]

    def tsukihime(*_args):
        return ([_cand("tsukihime", "b.ass", match=False)], ["tsukihime: search truncated"])

    monkeypatch.setattr(subselect, "_jimaku_candidates", jimaku)
    monkeypatch.setattr(subselect, "_tsukihime_candidates", tsukihime)

    candidates, warnings = subselect.list_candidates("/v/Show - 01.mkv", ("jimaku", "tsukihime"))

    assert [(c.provider, c.name) for c in candidates] == [
        ("jimaku", "a.srt"),
        ("tsukihime", "b.ass"),
    ]
    assert warnings == ["tsukihime: search truncated"]


def test_list_candidates_turns_a_provider_failure_into_a_warning(monkeypatch):
    """One dead provider must not blank the panel — it contributes a warning and the others still list."""

    def boom(*_args):
        raise RuntimeError("no entry")

    def tsukihime(*_args):
        candidate = subselect.SubtitleCandidate(
            provider="tsukihime", name="b.ass", size=0, match=False, download=lambda: (None, "")
        )
        return ([candidate], [])

    monkeypatch.setattr(subselect, "_jimaku_candidates", boom)
    monkeypatch.setattr(subselect, "_tsukihime_candidates", tsukihime)

    candidates, warnings = subselect.list_candidates("/v/Show - 01.mkv", ("jimaku", "tsukihime"))

    assert [c.provider for c in candidates] == ["tsukihime"]
    assert warnings == ["jimaku: no entry"]


# --- shared run/attach provider wiring -------------------------------------------------------------


class _FakeReader:
    def __init__(self):
        self.retry_factory = "unset"
        self.picker_lister = "unset"
        self.target_language = "unset"

    def configure_subtitle_retry(self, factory, *, target_language="jp"):
        self.retry_factory = factory
        self.target_language = target_language

    def configure_sub_picker(self, lister):
        self.picker_lister = lister


def test_provider_fetch_factory_defers_fetch_and_forwards_every_arg(monkeypatch):
    seen: dict = {}

    def fake_fetch(video, providers, **kwargs):
        seen.update(video=video, providers=providers, **kwargs)
        return None, "ok"

    monkeypatch.setattr(subselect, "fetch_provider_path", fake_fetch)
    factory = subselect.provider_fetch_factory(
        ("jimaku",),
        subselect.ProviderConfig(
            jimaku_key="K",
            jimaku_title="Show",
            episode=3,
            resync=False,
            tsukihime_config={"enabled": True},
        ),
        force=True,
    )
    thunk = factory("/v/Show - 03.mkv")

    assert seen == {}  # deferred — nothing fetches until the thunk is called
    assert thunk() == (None, "ok")
    assert seen == {
        "video": "/v/Show - 03.mkv",
        "providers": ("jimaku",),
        "jimaku_key": "K",
        "title_override": "Show",
        "episode": 3,
        "resync": False,
        "force": True,
        "tsukihime_config": {"enabled": True},
    }


def test_configure_providers_wires_retry_and_picker():
    reader = _FakeReader()
    subselect.configure_providers(
        reader.configure_subtitle_retry,
        reader.configure_sub_picker,
        subselect.ProviderConfig(
            enabled_providers=("jimaku", "tsukihime"),
            tsukihime_config={},
            language="fr",
        ),
    )
    assert callable(reader.retry_factory)  # a force-refetch retry factory
    assert callable(reader.picker_lister)  # the Ctrl+J source picker
    assert reader.target_language == "fr"


def test_configure_providers_clears_runtime_callbacks_when_no_provider():
    reader = _FakeReader()
    subselect.configure_providers(
        reader.configure_subtitle_retry,
        reader.configure_sub_picker,
        subselect.ProviderConfig(enabled_providers=("jimaku",)),
    )

    subselect.configure_providers(
        reader.configure_subtitle_retry, reader.configure_sub_picker, subselect.ProviderConfig()
    )

    assert reader.retry_factory is None
    assert reader.picker_lister is None


def test_configure_providers_retry_forces_a_refetch(monkeypatch):
    """The shared retry factory must pass force=True so a stale/mistimed cached srt is re-fetched."""
    seen: dict = {}
    monkeypatch.setattr(
        subselect,
        "fetch_provider_path",
        lambda _video, _providers, **kw: (seen.update(kw), (None, "ok"))[1],
    )
    reader = _FakeReader()
    subselect.configure_providers(
        reader.configure_subtitle_retry,
        reader.configure_sub_picker,
        subselect.ProviderConfig(enabled_providers=("jimaku",), tsukihime_config={}),
    )

    reader.retry_factory("/v/x.mkv")()  # factory(video) → thunk → fetch_provider_path

    assert seen["force"] is True


def _attach_opts(**overrides):
    return subselect.AttachSubtitleOptions(slang="ja,jpn,jp", jimaku=True, **overrides)


def test_attach_selects_a_cached_subtitle_before_falling_back_to_english(tmp_path, monkeypatch):
    """`run` resolves the cache before mpv launches, so it never shows the wrong language. Filing an
    on-disk file under the deferred providers instead puts a local stat behind the session's first
    owner-thread drain — English on screen for the whole of startup, then a visible flip."""
    cached = tmp_path / "Show-ep1-raw.ass"
    cached.write_text("[Events]\n", encoding="utf-8")
    monkeypatch.setattr(
        subselect, "_cached_subtitle", lambda *_a, **_kw: (cached, "cache: using it")
    )
    ipc = FakeIPC(tracks=[EN], path="/videos/Show - 01.mkv")

    startup, status, providers = subselect.prepare_attach_startup(ipc, _attach_opts())

    assert ("sub-add", str(cached), "auto", "", "jpn") in ipc.calls
    assert startup.tracks.jp_sid == 9  # the added track, picked up by `select_initial`
    assert status == "cache: using it"
    assert providers == ()  # nothing left to defer once the file is on screen


def test_attach_leaves_an_existing_japanese_track_alone(monkeypatch):
    """A file that ships JP subs is already correct, and adding a cached external over it would
    stack a second track the user never asked for."""
    probed = []
    monkeypatch.setattr(
        subselect, "_cached_subtitle", lambda *_a, **_kw: (probed.append(1), (None, None))[1]
    )
    ipc = FakeIPC(tracks=[EN, JP], path="/videos/Show - 01.mkv")

    startup, _status, _providers = subselect.prepare_attach_startup(ipc, _attach_opts())

    assert probed == []  # the cache is not even consulted
    assert startup.tracks.jp_sid == 2
    assert not [call for call in ipc.calls if call[0] == "sub-add"]


def test_attach_does_not_hand_a_japanese_cache_to_another_language(monkeypatch):
    """Both providers are Japanese-only, so a JP file cached from a prior run must not load as an
    external track over a second-language profile's own subtitles."""
    probed = []
    monkeypatch.setattr(
        subselect, "_cached_subtitle", lambda *_a, **_kw: (probed.append(1), (None, None))[1]
    )
    ipc = FakeIPC(tracks=[EN], path="/videos/Show - 01.mkv")

    subselect.prepare_attach_startup(ipc, _attach_opts(language="fr"))

    assert probed == []
