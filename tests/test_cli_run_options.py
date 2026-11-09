"""overlay.toml keys → ReaderOptions. Pure functions, no IPC/mpv."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from dirty_equals import IsPartialDict

from saitenka.app.commands.attach import _build_attach_options
from saitenka.app.launch.run import RunFlags, _build_run_options

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


def test_attach_options_read_hover_pause_settings():
    opts = _build_attach_options(
        {
            "pause_on_tooltip": False,
            "hover_pause_key": "Alt+q",
            "subtitle_language_key": "Alt+l",
            "bookmark_key": "Alt+b",
            "sidebar_key": "Alt+s",
            "analysis_key": "Ctrl+d",
            "annotation_key": "Ctrl+a",
            "help_key": "Ctrl+h",
            "subtitle_retry_key": "Ctrl+r",
            "annotation_mode": "hover",
            "stats": {"enabled": True, "summary": False},
        },
        mine={},
    )
    assert opts.tooltip.pause_on_tooltip is False
    assert opts.keys.hover_pause_key == "Alt+q"
    assert opts.keys.subtitle_language_key == "Alt+l"
    assert opts.keys.bookmark_key == "Alt+b"
    assert opts.keys.sidebar_key == "Alt+s"
    assert opts.keys.analysis_key == "Ctrl+d"
    assert opts.keys.annotation_key == "Ctrl+a"
    assert opts.keys.help_key == "Ctrl+h"
    assert opts.keys.subtitle_retry_key == "Ctrl+r"
    assert opts.tooltip.annotation_mode == "hover"
    assert opts.stats.enabled is True
    assert opts.stats.summary is False


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


_REFERENCE = json.loads((Path(__file__).parent / "fixtures/config_reference.json").read_text())


@pytest.mark.parametrize("record", _REFERENCE["run"], ids=lambda r: r["id"])
def test_run_configuration_reference(record):
    value = record["input"]
    options = _build_run_options(value["config"], replace(_BASE_FLAGS, **value["flags"]))
    assert asdict(options) == IsPartialDict(
        **{name: IsPartialDict(**fields) for name, fields in record["expected"].items()}
    )


@pytest.mark.parametrize("record", _REFERENCE["attach"], ids=lambda r: r["id"])
def test_attach_configuration_reference(record):
    value = record["input"]
    options = _build_attach_options(value["config"], mine=value["mine"])
    assert asdict(options) == IsPartialDict(
        **{name: IsPartialDict(**fields) for name, fields in record["expected"].items()}
    )


def test_run_preserves_profile_scoped_tables_and_flag_precedence(monkeypatch):
    from saitenka.app.features.profiles import dependencies
    from saitenka.app.launch import run

    captured = {}

    def build(cfg, **_kwargs):
        captured.update(cfg)
        return None, None, None, None

    monkeypatch.setattr(dependencies, "build_reader_deps", build)
    run._build_run_deps(
        run.RunDepsRequest(
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
            color=False,
            dict_titles=["from the flag"],
            freq_titles=[],
            pitch_titles=[],
            scoped_cfg={
                "palette": {"learning": "#eed49f"},
                "fsrs": {"collection": "/tmp/copy.anki2"},
                "scoring": {"jlpt_underlines": False},
                "dicts": ["from config"],
            },
        )
    )
    assert captured == IsPartialDict(
        palette={"learning": "#eed49f"},
        fsrs={"collection": "/tmp/copy.anki2"},
        scoring={"jlpt_underlines": False},
        dicts=["from the flag"],
    )
