"""Stage 8a: cyclopts CLI — the flag inventory is the contract (this test pins it; mpv_reader.py wraps it).

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
    "import-settings",
    "install-plugin",
    "uninstall-plugin",
    "attach",
    "setup",
]


def _cli_app():
    from saitenka.app.cli import app

    return app


def test_cli_flag_inventory_matches_mpv_reader():
    """Every legacy flag must exist on the `run` command with its exact spelling."""
    out = subprocess.run(
        [sys.executable, "-m", "saitenka.app.cli", "run", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",  # child emits UTF-8; Windows would otherwise decode as cp1252 and crash
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    help_text = out.stdout
    missing = [f for f in EXPECTED_FLAGS if f not in help_text]
    assert not missing, f"flags missing from `run --help`: {missing}"


def test_cli_has_subcommand_skeleton():
    out = subprocess.run(
        [sys.executable, "-m", "saitenka.app.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",  # child emits UTF-8; Windows would otherwise decode as cp1252 and crash
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    for cmd in SUBCOMMANDS:
        assert cmd in out.stdout, f"subcommand {cmd} missing from --help"


def test_every_command_help_stands_up():
    """Registry-driven smoke: EVERY registered command's ``--help`` binds its full signature and
    exits 0, so a command that breaks on load (bad annotation, missing import, cyclopts wiring) is
    caught the moment it's added — no hardcoded list to drift like ``SUBCOMMANDS`` above. In-process,
    no subprocess/mpv/network. NOT a live test: ``--help`` short-circuits before the command body, so
    this proves the wiring stands up, not that a command *runs* (that's the ``live`` gate's job)."""
    import contextlib
    import io

    app = _cli_app()
    commands = [n for n in app if not n.startswith("-")]  # skip -h/--help/--version flag entries
    assert {"run", "doctor", "attach"} <= set(commands)  # enumeration actually found real commands
    for name in commands:
        with contextlib.redirect_stdout(io.StringIO()), pytest.raises(SystemExit) as exc:
            app([name, "--help"], exit_on_error=False)
        assert exc.value.code == 0, f"`{name} --help` exited {exc.value.code}"


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


def test_no_mine_preview_flag_toggles_the_panel():
    """--no-mine-preview turns off the post-mine card-preview panel; default is on."""
    app = _cli_app()
    _, bound, *_ = app.parse_args(["run", "--no-mine-preview"])
    assert bound.arguments["mine_preview"] is False
    _, bound2, *_ = app.parse_args(["run"])
    bound2.apply_defaults()
    assert bound2.arguments["mine_preview"] is True


def test_defaults_match_legacy(tmp_path, monkeypatch):
    # Isolate from the developer's real overlay.toml (cyclopts config.Toml feeds it as defaults) by
    # pointing at an absent file and reloading — this tests the PARAMETER defaults, deterministically.
    import importlib

    from saitenka.app import cli

    monkeypatch.setenv("SAITENKA_CONFIG", str(tmp_path / "absent.toml"))
    importlib.reload(cli)
    try:
        _, bound, *_ = cli.app.parse_args(["run"])
        bound.apply_defaults()
        kw = bound.arguments
        assert kw["slang"] == "ja,jpn,jp"
        assert kw["start"] == "1"
        assert kw["width"] == 1920 and kw["height"] == 1080
        assert kw["seconds"] == pytest.approx(60.0)
        assert kw["tip_height"] == pytest.approx(0.4)
        assert kw["hover_switch_delay"] == pytest.approx(0.15)
        # deck/model default to None (flag-not-passed sentinel, #254) — the concrete deck/model is
        # resolved at runtime from the active profile / runtime [mine], not baked into the signature.
        assert kw["mine_deck"] is None and kw["mine_model"] is None
        assert kw["mine_key"] == "Ctrl+m" and kw["mine_all_key"] == "Shift+m"
        assert kw["translate_key"] == "t" and kw["preview_key"] == "p"
        assert kw["video"] is None
        assert kw["demo_scroll"] == 0
    finally:
        monkeypatch.undo()  # restore the env, then rebind cli to the default config for later tests
        importlib.reload(cli)


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

    from saitenka.app import cli

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
    """examples/mpv_reader.py must delegate to saitenka.app.cli (no argparse of its own)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "examples" / "mpv_reader.py").read_text()
    assert "saitenka.app.cli" in src
    assert "argparse" not in src


def test_resolve_names_flag_wins_over_config():
    """dict/freq/pitch are dictionary TITLES resolved against the DB: explicit flags win over the
    config file, and both are passed through verbatim (titles, not paths — no ~-expansion)."""
    from saitenka.app.cli import _resolve_names

    assert _resolve_names(["FlagDict"], {"dicts": ["ConfigDict"]}, "dicts") == ["FlagDict"]
    assert _resolve_names([], {"dicts": ["ConfigDict"]}, "dicts") == ["ConfigDict"]
    assert _resolve_names(None, {}, "dicts") == []


def test_version_is_wired_to_package_metadata():
    """`--version` printed 0.0.0 because cyclopts couldn't resolve the `overlay` import package to the
    `saitenka` distribution — now pinned via saitenka.__version__."""
    from importlib.metadata import version

    import saitenka
    from saitenka.app.cli import app

    assert saitenka.__version__ == version("saitenka")
    assert app.version not in {None, "", "0.0.0"}


def test_jimaku_should_fetch_decision():
    """run fetches jimaku on an explicit flag always; config-driven fetch only when there's no
    embedded JP track (so it never overrides good embedded subs)."""
    from saitenka.app.cli import jimaku_should_fetch as f

    assert (
        f(explicit_flag=True, cfg_fetch=False, video="v.mkv", probe=lambda _v, _s: True) is True
    )  # --jimaku wins over embedded JP
    assert f(explicit_flag=True, cfg_fetch=True, video=None) is False  # no video → never
    assert (
        f(explicit_flag=False, cfg_fetch=True, video="v.mkv", probe=lambda _v, _s: False) is True
    )  # config + no JP track → fetch
    assert (
        f(explicit_flag=False, cfg_fetch=True, video="v.mkv", probe=lambda _v, _s: True) is False
    )  # config + JP track → skip
    assert (
        f(explicit_flag=False, cfg_fetch=True, video="v.mkv", probe=lambda _v, _s: None) is True
    )  # config + can't probe → fetch
    assert (
        f(explicit_flag=False, cfg_fetch=False, video="v.mkv", probe=lambda _v, _s: False) is False
    )  # neither → never


def test_resolve_atlas_scale_explicit_value_wins():
    from saitenka.app.cli import _resolve_atlas_scale

    assert (
        _resolve_atlas_scale({"tip_scale": 1.5}, 2.0) == 2.0
    )  # an explicit --atlas-scale > 0 is used


def test_resolve_atlas_scale_zero_inherits_top_level_tip_scale():
    from saitenka.app.cli import _resolve_atlas_scale

    assert _resolve_atlas_scale({"tip_scale": 1.5}, 0.0) == 1.5  # 0 → the runtime's tip_scale


def test_resolve_atlas_scale_ignores_nested_tooltip_key_like_the_runtime():
    from saitenka.app.cli import _resolve_atlas_scale

    # The runtime reads only top-level tip_scale; a nested [tooltip] tip_scale it ignores must NOT be
    # honoured here either, or prewarm would build a scale the tooltip never displays.
    assert _resolve_atlas_scale({"tooltip": {"tip_scale": 1.5}}, 0.0) == 1.0


def test_resolve_atlas_scale_defaults_to_reference_when_unset():
    from saitenka.app.cli import _resolve_atlas_scale

    assert _resolve_atlas_scale({}, 0.0) == 1.0  # nothing configured → reference only
