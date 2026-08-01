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


def test_run_options_read_hover_pause_key():
    opts = _build_run_options({"hover_pause_key": "Alt+q"}, **_BASE_KWARGS)
    assert opts.keys.hover_pause_key == "Alt+q"


def test_attach_options_read_hover_pause_settings():
    opts = _build_attach_options({"pause_on_tooltip": False, "hover_pause_key": "Alt+q"}, mine={})
    assert opts.tooltip.pause_on_tooltip is False
    assert opts.keys.hover_pause_key == "Alt+q"
