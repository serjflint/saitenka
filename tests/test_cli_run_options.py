"""overlay.toml keys → ReaderOptions. Pure functions, no IPC/mpv."""

from dataclasses import replace

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


def test_scan_delay_defaults_when_absent_from_config():
    opts = _build_run_options({}, _BASE_FLAGS)
    assert opts.tooltip.scan_delay == 1.0


def test_run_options_pass_layout_engine_through():
    opts = _build_run_options({}, replace(_BASE_FLAGS, layout_engine="taffy"))
    assert opts.tooltip.layout_engine == "taffy"


def test_attach_options_read_layout_engine_from_config():
    opts = _build_attach_options({"layout_engine": "taffy"}, mine={})
    assert opts.tooltip.layout_engine == "taffy"


def test_scan_delay_reads_from_config():
    opts = _build_run_options({"scan_delay": 1.5}, _BASE_FLAGS)
    assert opts.tooltip.scan_delay == 1.5


def test_run_options_pass_tip_scale_through():
    opts = _build_run_options({}, replace(_BASE_FLAGS, tip_scale=1.5))
    assert opts.tooltip.tip_scale == 1.5


def test_attach_options_read_tip_scale_from_config():
    # The attach path reads it from config (not a CLI kwarg) — must match the run path or the knob
    # silently no-ops when attaching to a running mpv.
    assert _build_attach_options({}, mine={}).tooltip.tip_scale == 0.0  # default = auto
    assert _build_attach_options({"tip_scale": 1.5}, mine={}).tooltip.tip_scale == 1.5


def test_run_options_read_utility_ui_scale():
    opts = _build_run_options({"ui_scale": 1.5}, _BASE_FLAGS)
    assert opts.panels.scale == 1.5


def test_attach_options_read_utility_ui_scale():
    opts = _build_attach_options({"ui_scale": 1.5}, mine={})
    assert opts.panels.scale == 1.5


def test_attach_options_read_mine_preview_toggle():
    assert _build_attach_options({}, mine={}).mining.show_preview is True  # default on
    opts = _build_attach_options({}, mine={"preview": False})
    assert opts.mining.show_preview is False  # [mine] preview = false disables the panel


def test_run_options_read_mine_preview_toggle():
    assert (
        _build_run_options({}, replace(_BASE_FLAGS, mine_preview=False)).mining.show_preview
        is False
    )


def test_run_path_threads_animated_screenshot_into_effective_cfg(monkeypatch):
    # The RUN path rebuilds a synthetic effective_cfg dict from the CLI kwargs; the animated flag must
    # survive it or it silently no-ops on `run` while working on `attach` (the both-seams trap).
    from saitenka.app import reader_deps
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
            raw_mine={"animated_height": 720, "animated_format": "gif", "deck": "IGNORED"},
            known_cfg=None,
            known="",
            color=False,
            dict_titles=[],
            freq_titles=[],
            pitch_titles=[],
        )
    )
    assert captured["mine"]["animated_screenshot"] is True
    # config-only quality knobs (no CLI flag) must survive the synthetic effective_cfg too
    assert captured["mine"]["animated_height"] == 720
    assert captured["mine"]["animated_format"] == "gif"
    assert captured["mine"]["deck"] == "D"  # CLI-threaded value overrides the raw config


def test_resolve_mine_model_prefers_explicit_then_preset_then_lapis():
    # preset-only config must resolve to the preset's note type on run/doctor, matching the attach
    # seam's _mine_config_from — else `preset = "Kiku"` (no model) mines to Kiku on attach but Lapis on run.
    from saitenka.app.command_defaults import resolve_mine_model

    assert resolve_mine_model({}) == "Lapis"
    assert resolve_mine_model({"preset": "Kiku"}) == "Kiku"
    assert (
        resolve_mine_model({"model": "Custom", "preset": "Kiku"}) == "Custom"
    )  # explicit model wins


def test_run_threads_field_map_and_card_kind(monkeypatch):
    # #101 field-map/card-kind have no CLI flag — they must ride through the RUN seam purely via the
    # raw [mine] table (the both-seams trap), or they'd work on attach but silently no-op on run.
    from saitenka.app import reader_deps
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
            mine_animated_screenshot=False,
            raw_mine={"preset": "Kiku", "card_kind": "sentence", "fields": {"expression": "Word"}},
            known_cfg=None,
            known="",
            color=False,
            dict_titles=[],
            freq_titles=[],
            pitch_titles=[],
        )
    )
    assert captured["mine"]["preset"] == "Kiku"
    assert captured["mine"]["card_kind"] == "sentence"
    assert captured["mine"]["fields"] == {"expression": "Word"}


def test_run_threads_card_format(monkeypatch):
    # [mine.card_format] has no CLI flag — like fields/card_kind it must ride the RUN seam via raw [mine].
    from saitenka.app import reader_deps
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
            mine_animated_screenshot=False,
            raw_mine={"card_format": {"Word": "{expression}"}},
            known_cfg=None,
            known="",
            color=False,
            dict_titles=[],
            freq_titles=[],
            pitch_titles=[],
        )
    )
    assert captured["mine"]["card_format"] == {"Word": "{expression}"}


def test_run_options_read_hover_pause_key():
    opts = _build_run_options(
        {
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
        _BASE_FLAGS,
    )
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
