"""overlay.toml keys → ReaderOptions. Pure functions, no IPC/mpv."""

from overlay.app.cli import _build_attach_options
from overlay.app.cli_run import _build_run_options

_BASE_KWARGS = {
    "mine_key": "Ctrl+m",
    "mine_all_key": "Shift+m",
    "translate_key": "t",
    "preview_key": "p",
    "tip_height": 0.4,
    "dict_tabs": False,
    "pause_on_tooltip": True,
    "hover_switch_delay": 0.15,
    "no_audio_play": False,
    "auto_translate": False,
    "prefetch": True,
}


def test_scan_delay_defaults_when_absent_from_config():
    opts = _build_run_options({}, **_BASE_KWARGS)
    assert opts.tooltip.scan_delay == 0.25


def test_scan_delay_reads_from_config():
    opts = _build_run_options({"scan_delay": 1.5}, **_BASE_KWARGS)
    assert opts.tooltip.scan_delay == 1.5


def test_run_options_read_utility_ui_scale():
    opts = _build_run_options({"ui_scale": 1.5}, **_BASE_KWARGS)
    assert opts.panels.scale == 1.5


def test_attach_options_read_utility_ui_scale():
    opts = _build_attach_options({"ui_scale": 1.5}, mine={})
    assert opts.panels.scale == 1.5


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
        **_BASE_KWARGS,
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
