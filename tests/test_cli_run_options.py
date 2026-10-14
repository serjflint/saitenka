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


def test_resolve_mine_model_prefers_explicit_then_preset_then_lapis():
    # preset-only config must resolve to the preset's note type on run/doctor, matching the attach
    # seam's mine_config_from — else `preset = "Kiku"` (no model) mines to Kiku on attach but Lapis on run.
    from saitenka.app.command_defaults import resolve_mine_model

    assert resolve_mine_model({}) == "Lapis"
    assert resolve_mine_model({"preset": "Kiku"}) == "Kiku"
    assert (
        resolve_mine_model({"model": "Custom", "preset": "Kiku"}) == "Custom"
    )  # explicit model wins


def test_run_carries_every_config_table_the_dependency_build_reads():
    """`run` used to rebuild its effective config from a hand-written key list, so a table it
    forgot worked under `attach` and silently did nothing under `run` — the setting keeps its
    default, the config file says otherwise, and nothing warns.

    Three had already fallen through when this was written: `[palette]` (band colors), `[fsrs]`
    (maturity from Anki) and `[scoring]`. The language field carries a comment about the same trap,
    paid for once already as #254. Named tables rather than a fixed list, because the point is that
    the config reaches the builder whole.
    """
    from saitenka.app.launch.run import RunDepsRequest, _run_effective_config

    scoped = {
        "palette": {"learning": "#eed49f"},
        "fsrs": {"collection": "/tmp/copy.anki2"},
        "scoring": {"jlpt_underlines": False},
        "dicts": ["from config"],
    }
    request = RunDepsRequest(
        mine=False,
        mine_deck="",
        mine_model="",
        mine_key="",
        mine_all_key="",
        mine_normalize_audio=False,
        mine_animated_screenshot=False,
        raw_mine={},
        known_cfg=None,
        known="",
        color=True,
        dict_titles=["from the flag"],
        freq_titles=[],
        pitch_titles=[],
        scoped_cfg=scoped,
    )

    effective = _run_effective_config(request)

    assert effective["palette"] == {"learning": "#eed49f"}
    assert effective["fsrs"] == {"collection": "/tmp/copy.anki2"}
    assert effective["scoring"] == {"jlpt_underlines": False}
    # …and the run's own options still win over the file they are layered onto.
    assert effective["dicts"] == ["from the flag"]
