"""``_build_run_options`` (cli_run.py): overlay.toml keys → ReaderOptions. Pure function, no IPC/mpv —
covers the cfg.get(...) wiring lines directly rather than exercising the whole `run` CLI command."""

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
