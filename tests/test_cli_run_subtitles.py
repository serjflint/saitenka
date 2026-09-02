"""Run-mode subtitle startup decisions that happen before the live mpv boundary."""

from pathlib import Path

from saitenka.app import jimaku as jimaku_mod
from saitenka.app import subselect, subtitle_cache
from saitenka.app.episode_reslot import ReslotPorts
from saitenka.app.launch import run as cli_run


def test_run_subtitle_options_preserves_the_positional_sub_file_argument():
    options = cli_run.RunSubtitleOptions("ja,jpn,jp", "episode.srt")

    assert options.sub_file == "episode.srt" and options.second_slang == "en"


def _resolve(tmp_path, *, jimaku: bool):
    return cli_run._resolve_subtitles(
        {"jimaku": {"fetch": True}},
        "episode.mkv",
        tmp_path / "episode.mkv",
        30,
        tmp_path,
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp", jimaku=jimaku),
        jimaku_title=None,
        episode=None,
    )


def test_configured_run_fetch_is_deferred_until_after_english_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cli_run,
        "jimaku_should_fetch",
        lambda **kwargs: kwargs["explicit_flag"] or kwargs["cfg_fetch"],
    )
    monkeypatch.setattr(
        cli_run,
        "_resolve_jimaku_subs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("configured provider must not block mpv startup")
        ),
    )
    sub_path, en_path, fetch_in_background, enabled = _resolve(tmp_path, jimaku=False)
    assert sub_path is None and en_path is None
    assert fetch_in_background == ("jimaku",)
    assert enabled == ("jimaku",)


def test_configured_run_uses_cached_jimaku_subtitle_before_launch(tmp_path, monkeypatch):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **kwargs: kwargs["cfg_fetch"])
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")
    cached = jimaku_mod.store_subs(video, "Show", 1, downloaded)

    sub_path, _en_path, background, enabled = cli_run._resolve_subtitles(
        {"jimaku": {"fetch": True}},
        str(video),
        video,
        30,
        tmp_path,
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp", resync=True),
        jimaku_title=None,
        episode=None,
    )

    assert sub_path == cached
    assert background == ()
    assert enabled == ("jimaku",)


def test_configured_run_uses_shared_cache_with_only_tsukihime_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("SAITENKA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **kwargs: kwargs["cfg_fetch"])
    video = tmp_path / "Show - 01.mkv"
    video.write_bytes(b"video")
    downloaded = tmp_path / "downloaded.srt"
    downloaded.write_text("Japanese", encoding="utf-8")
    cached = subtitle_cache.store_subs(video, "Show", 1, downloaded, resync=True)

    sub_path, _en_path, background, enabled = cli_run._resolve_subtitles(
        {"tsukihime": {"enabled": True}},
        str(video),
        video,
        30,
        tmp_path,
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp", resync=True),
        jimaku_title=None,
        episode=None,
    )

    assert sub_path == cached
    assert background == ()
    assert enabled == ("tsukihime",)


def test_explicit_run_jimaku_retains_synchronous_override(tmp_path, monkeypatch):
    fetched = tmp_path / "explicit.ja.srt"
    monkeypatch.setattr(
        cli_run,
        "jimaku_should_fetch",
        lambda **kwargs: kwargs["explicit_flag"] or kwargs["cfg_fetch"],
    )
    monkeypatch.setattr(cli_run, "_resolve_jimaku_subs", lambda *_args, **_kwargs: fetched)
    sub_path, _en_path, fetch_in_background, enabled = _resolve(tmp_path, jimaku=True)
    assert sub_path == fetched
    assert fetch_in_background == ()
    assert enabled == ("jimaku",)


def test_tsukihime_is_background_only_and_follows_jimaku(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **kwargs: kwargs["cfg_fetch"])

    _sub_path, _en_path, providers, enabled = cli_run._resolve_subtitles(
        {"jimaku": {"fetch": True}, "tsukihime": {"enabled": True}},
        "episode.mkv",
        tmp_path / "episode.mkv",
        30,
        tmp_path,
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp"),
        jimaku_title=None,
        episode=None,
    )

    assert providers == ("jimaku", "tsukihime")
    assert enabled == providers


def test_disabled_tsukihime_does_not_enter_run_provider_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **kwargs: kwargs["cfg_fetch"])

    _sub_path, _en_path, providers, enabled = cli_run._resolve_subtitles(
        {"tsukihime": {"enabled": False}},
        "episode.mkv",
        tmp_path / "episode.mkv",
        30,
        tmp_path,
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp"),
        jimaku_title=None,
        episode=None,
    )

    assert providers == ()
    assert enabled == ()


def test_embedded_japanese_skips_startup_fetch_but_keeps_runtime_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **_kwargs: False)

    _sub_path, _en_path, background, enabled = cli_run._resolve_subtitles(
        {"jimaku": {"enabled": True}, "tsukihime": {"enabled": True}},
        "episode.mkv",
        tmp_path / "episode.mkv",
        30,
        tmp_path,
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp"),
        jimaku_title=None,
        episode=None,
    )

    assert background == ()
    assert enabled == ("jimaku", "tsukihime")


def test_run_retry_factory_uses_current_media_and_provider_order(tmp_path, monkeypatch):
    calls = []

    class SessionController:
        retry_factory = None
        startup_fetch = None
        picker_lister = None

        def configure_subtitle_retry(self, factory):
            self.retry_factory = factory

        def fetch_japanese_subs_async(self, fetch):
            self.startup_fetch = fetch

        def configure_sub_picker(self, lister):
            self.picker_lister = lister

        @property
        def reslot_ports(self):
            """The shape `SessionController.reslot_ports` builds — the seam the fetch is driven through."""
            return ReslotPorts(
                ipc=None,
                finish_stats=lambda: None,
                start_stats=lambda: None,
                rebind_episode=lambda: None,
                rebuild_index=lambda: None,
                configure_mode=lambda *_a, **_kw: None,
                configure_retry=self.configure_subtitle_retry,
                configure_picker=self.configure_sub_picker,
                fetch_japanese=self.fetch_japanese_subs_async,
                start_prefetch=lambda: None,
                toast=lambda *_a, **_kw: None,
            )

    def fetch(video, providers, **_kwargs):
        calls.append((video, providers))
        return tmp_path / "episode.ja.srt", "tsukihime: added episode.ja.srt"

    monkeypatch.setattr(subselect, "fetch_provider_path", fetch)
    reader = SessionController()

    cli_run._start_run_provider_fetch(
        reader.reslot_ports,
        {"jimaku": {"enabled": True}, "tsukihime": {"enabled": True}},
        tmp_path / "old.mkv",
        cli_run.RunSubtitleOptions(slang="ja,jpn,jp"),
        providers=(),
        enabled_providers=("jimaku", "tsukihime"),
        jimaku_title=None,
        episode=None,
    )

    assert reader.startup_fetch is None
    assert reader.retry_factory is not None
    path, status = reader.retry_factory("/videos/current.mkv")()
    assert path == Path(tmp_path / "episode.ja.srt")
    assert status == "tsukihime: added episode.ja.srt"
    assert calls == [("/videos/current.mkv", ("jimaku", "tsukihime"))]
