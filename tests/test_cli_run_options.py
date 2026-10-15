"""overlay.toml keys → ReaderOptions. Pure functions, no IPC/mpv."""

from dirty_equals import IsPartialDict

from saitenka.app.launch.run import RunFlags

_BASE_FLAGS = RunFlags(
    mine_key="Ctrl+m",
    mine_all_key="Shift+m",
    translate_key="t",
    preview_key="p",
    tip_height=0.4,
    tip_scale=0.0,
    pause_on_tooltip=True,
    hover_switch_delay=0.15,
    no_audio_play=False,
    mine_preview=True,
    auto_translate=False,
    prefetch=True,
    layout_engine="default",
)


def test_run_threads_mining_config_into_dependency_build(monkeypatch):
    import saitenka.app.features.profiles.dependencies as reader_deps
    from saitenka.app.launch import run as cli_run

    captured: dict = {}
    monkeypatch.setattr(
        reader_deps,
        "build_reader_deps",
        lambda cfg, **_k: (captured.update(cfg), (None, None, None, None))[1],
    )
    cli_run._build_run_deps(
        cli_run.RunDepsRequest(
            mine=True,
            mine_deck="D",
            mine_model="Lapis",
            mine_key="Ctrl+m",
            mine_all_key="Shift+m",
            mine_normalize_audio=False,
            mine_animated_screenshot=True,
            raw_mine={
                "animated_height": 720,
                "animated_format": "gif",
                "preset": "Kiku",
                "card_kind": "sentence",
                "fields": {"expression": "Word"},
                "card_format": {"Word": "{expression}"},
                "deck": "IGNORED",
            },
            known_cfg=None,
            known="",
            color=False,
            dict_titles=[],
            freq_titles=[],
            pitch_titles=[],
        )
    )
    assert captured["mine"] == IsPartialDict(
        {
            "animated_height": 720,
            "animated_format": "gif",
            "preset": "Kiku",
            "card_kind": "sentence",
            "fields": {"expression": "Word"},
            "card_format": {"Word": "{expression}"},
            "deck": "D",
            "model": "Lapis",
            "normalize_audio": False,
            "animated_screenshot": True,
        }
    )
