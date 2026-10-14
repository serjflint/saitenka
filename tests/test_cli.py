"""Stage 8a: cyclopts CLI — the flag inventory is the contract (this test pins it; mpv_reader.py wraps it).

HARD CONSTRAINT: every existing mpv_reader.py flag keeps its exact name and repeatable/negation
behaviour. These tests pin the inventory and the parse semantics without launching mpv.
"""

import subprocess
import sys

import pytest


def _cli_app():
    from saitenka.app.cli import app

    return app


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


@pytest.mark.integration
@pytest.mark.timeout(5)
def test_legacy_mpv_reader_entrypoint_delegates_to_run_help():
    """The documented legacy entrypoint loads and exposes the current run command."""
    from pathlib import Path

    wrapper = Path(__file__).resolve().parent.parent / "examples" / "mpv_reader.py"
    result = subprocess.run(
        [sys.executable, str(wrapper), "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert "--sub-file" in result.stdout
    assert "--mine-key" in result.stdout


def test_resolve_names_flag_wins_over_config():
    """dict/freq/pitch are dictionary TITLES resolved against the DB: explicit flags win over the
    config file, and both are passed through verbatim (titles, not paths — no ~-expansion)."""
    from saitenka.app.launch.run import resolve_names

    assert resolve_names(["FlagDict"], {"dicts": ["ConfigDict"]}, "dicts") == ["FlagDict"]
    assert resolve_names([], {"dicts": ["ConfigDict"]}, "dicts") == ["ConfigDict"]
    assert resolve_names(None, {}, "dicts") == []


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


@pytest.mark.parametrize(
    ("config", "explicit", "expected"),
    [
        pytest.param({"tip_scale": 1.5}, 2.0, 2.0, id="explicit"),
        pytest.param({"tip_scale": 1.5}, 0.0, 1.5, id="top-level-tip-scale"),
        pytest.param({"tooltip": {"tip_scale": 1.5}}, 0.0, 1.0, id="nested-is-ignored"),
        pytest.param({}, 0.0, 1.0, id="reference-default"),
    ],
)
def test_prewarm_forwards_the_runtime_atlas_scale(monkeypatch, config, explicit, expected):
    from saitenka.app import prewarm as prewarm_module
    from saitenka.app.commands import diagnostics
    from saitenka.app.prewarm import PrewarmResult

    seen = []
    monkeypatch.setattr(diagnostics, "load_config", lambda: config)
    monkeypatch.setattr(
        prewarm_module,
        "prewarm",
        lambda *_args, opts, **_kwargs: (
            seen.append(opts)
            or PrewarmResult(
                candidates=0,
                stored=0,
                rows=0,
                bytes=0,
                skipped=0,
                stopped_at_ceiling=False,
            )
        ),
    )

    assert diagnostics.prewarm(limit=1, atlas_scale=explicit, atlas_only=True) == 0
    assert seen[0].atlas_scale == expected
