"""Run-mode subtitle startup decisions that happen before the live mpv boundary."""

from overlay.app import cli_run


def _resolve(tmp_path, *, jimaku: bool):
    return cli_run._resolve_subtitles(
        {"jimaku": {"fetch": True}},
        "episode.mkv",
        tmp_path / "episode.mkv",
        30,
        tmp_path,
        sub_file=None,
        jimaku=jimaku,
        jimaku_key=None,
        jimaku_title=None,
        episode=None,
        resync=False,
        slang="ja,jpn,jp",
    )


def test_configured_run_fetch_is_deferred_until_after_english_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **_kwargs: True)
    monkeypatch.setattr(
        cli_run,
        "_resolve_jimaku_subs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("configured provider must not block mpv startup")
        ),
    )
    sub_path, en_path, fetch_in_background = _resolve(tmp_path, jimaku=False)
    assert sub_path is None and en_path is None
    assert fetch_in_background is True


def test_explicit_run_jimaku_retains_synchronous_override(tmp_path, monkeypatch):
    fetched = tmp_path / "explicit.ja.srt"
    monkeypatch.setattr(cli_run, "jimaku_should_fetch", lambda **_kwargs: True)
    monkeypatch.setattr(cli_run, "_resolve_jimaku_subs", lambda *_args, **_kwargs: fetched)
    sub_path, _en_path, fetch_in_background = _resolve(tmp_path, jimaku=True)
    assert sub_path == fetched
    assert fetch_in_background is False
