"""Stage 8a: cyclopts CLI — the flag inventory is the contract (RUNNING.md / mpv_reader.py).

HARD CONSTRAINT: every existing mpv_reader.py flag keeps its exact name and repeatable/negation
behaviour. These tests pin the inventory and the parse semantics without launching mpv.
"""

import subprocess
import sys

import pytest

# The complete flag contract, extracted from examples/mpv_reader.py's argparse definition.
EXPECTED_FLAGS = [
    "--config",
    "--sub-file",
    "--slang",
    "--dict",
    "--translate-key",
    "--start",
    "--jimaku",
    "--jimaku-key",
    "--jimaku-title",
    "--episode",
    "--width",
    "--height",
    "--fullscreen",
    "--use-config",
    "--demo-word",
    "--demo-translate",
    "--demo-scroll",
    "--bulk",
    "--screenshot",
    "--seconds",
    "--color",
    "--known",
    "--anki-decks",
    "--freq",
    "--pitch",
    "--mine",
    "--mine-deck",
    "--mine-model",
    "--mine-key",
    "--mine-all-key",
    "--preview-key",
    "--no-audio-play",
    "--tip-height",
    "--pause-on-tooltip",
    "--no-prefetch",
    "--auto-translate",
    "--hover-switch-delay",
    "--no-resync",
]

SUBCOMMANDS = [
    "run",
    "doctor",
    "init",
    "import-yomitan",
    "install-plugin",
    "uninstall-plugin",
    "attach",
    "setup",
]


def _cli_app():
    from overlay.app.cli import app

    return app


def test_cli_flag_inventory_matches_mpv_reader():
    """Every legacy flag must exist on the `run` command with its exact spelling."""
    out = subprocess.run(
        [sys.executable, "-m", "overlay.app.cli", "run", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    help_text = out.stdout
    missing = [f for f in EXPECTED_FLAGS if f not in help_text]
    assert not missing, f"flags missing from `run --help`: {missing}"


def test_cli_has_subcommand_skeleton():
    out = subprocess.run(
        [sys.executable, "-m", "overlay.app.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    for cmd in SUBCOMMANDS:
        assert cmd in out.stdout, f"subcommand {cmd} missing from --help"


def test_repeatable_dict_freq_pitch_flags():
    """--dict/--freq/--pitch are repeatable, order-preserving (argparse append semantics)."""
    app = _cli_app()
    _cmd, bound, *_ = app.parse_args(
        [
            "run",
            "--dict",
            "a.zip",
            "--dict",
            "b.zip",
            "--freq",
            "f.zip",
            "--pitch",
            "p.zip",
            "--pitch",
            "q.zip",
        ],
    )
    kw = bound.arguments
    assert list(kw["dicts"]) == ["a.zip", "b.zip"]
    assert list(kw["freq"]) == ["f.zip"]
    assert list(kw["pitch"]) == ["p.zip", "q.zip"]


def test_negation_flags_keep_argparse_semantics():
    """--no-audio-play and --no-prefetch are standalone switches, exactly as before."""
    app = _cli_app()
    _, bound, *_ = app.parse_args(["run", "--no-audio-play", "--no-prefetch"])
    kw = bound.arguments
    assert kw["no_audio_play"] is True
    assert kw["prefetch"] is False
    _, bound2, *_ = app.parse_args(["run"])
    bound2.apply_defaults()
    kw2 = bound2.arguments
    assert kw2["no_audio_play"] is False
    assert kw2["prefetch"] is True


def test_defaults_match_legacy():
    app = _cli_app()
    _, bound, *_ = app.parse_args(["run"])
    bound.apply_defaults()
    kw = bound.arguments
    assert kw["slang"] == "ja,jpn,jp"
    assert kw["start"] == "1"
    assert kw["width"] == 1920 and kw["height"] == 1080
    assert kw["seconds"] == pytest.approx(60.0)
    assert kw["tip_height"] == pytest.approx(0.6)
    assert kw["hover_switch_delay"] == pytest.approx(0.15)
    assert kw["mine_deck"] == "Saitenka::Mining" and kw["mine_model"] == "Lapis"
    assert kw["mine_key"] == "Ctrl+m" and kw["mine_all_key"] == "Shift+m"
    assert kw["translate_key"] == "t" and kw["preview_key"] == "p"
    assert kw["video"] is None
    assert kw["demo_scroll"] == 0


def test_video_positional():
    app = _cli_app()
    _, bound, *_ = app.parse_args(["run", "/tmp/x.mkv"])
    assert bound.arguments["video"] == "/tmp/x.mkv"


def test_toml_config_feeds_defaults(tmp_path, monkeypatch):
    """cyclopts.config.Toml: values from the overlay TOML act as defaults, CLI flags override."""
    cfgfile = tmp_path / "overlay.toml"
    cfgfile.write_text('slang = "en,eng"\ntip_height = 0.4\npause_on_tooltip = true\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(cfgfile))
    import importlib

    import overlay.app.cli as cli

    importlib.reload(cli)  # pick up the env-pointed config path
    try:
        _, bound, *_ = cli.app.parse_args(["run"])
        kw = bound.arguments
        assert kw["slang"] == "en,eng"  # TOML default applied
        assert kw["tip_height"] == pytest.approx(0.4)
        assert kw["pause_on_tooltip"] is True
        _, bound2, *_ = cli.app.parse_args(["run", "--slang", "ja"])
        assert bound2.arguments["slang"] == "ja"  # explicit CLI flag still wins
    finally:
        monkeypatch.delenv("SAITENKA_CONFIG")
        importlib.reload(cli)


def test_mpv_reader_is_thin_wrapper():
    """examples/mpv_reader.py must delegate to overlay.app.cli (no argparse of its own)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "examples" / "mpv_reader.py").read_text()
    assert "overlay.app.cli" in src
    assert "argparse" not in src


def test_resolve_paths_expands_tilde_from_flags_and_config(monkeypatch, tmp_path):
    """TOML-sourced values arrive through the CLI parameter (cyclopts config.Toml),
    so ~-expansion must happen on the flag side too, not only on the cfg fallback."""
    from overlay.app.cli import _resolve_paths

    monkeypatch.setenv("HOME", str(tmp_path))
    got = _resolve_paths(["~/dicts/a.zip"], {"dicts": ["~/dicts/b.zip"]}, "dicts")
    assert got == [str(tmp_path / "dicts/a.zip")]  # flag wins, expanded
    got = _resolve_paths([], {"dicts": ["~/dicts/b.zip"]}, "dicts")
    assert got == [str(tmp_path / "dicts/b.zip")]  # cfg fallback, expanded
