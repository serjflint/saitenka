"""Profile commands persist the intended configuration and report resolved values."""

from __future__ import annotations

import tomllib

import pytest

from saitenka.app.features.profiles.profile_cli import (
    add_proposal,
    build_profile_table,
    profile_names,
    render_list,
    render_show,
)


@pytest.mark.parametrize(
    ("flags", "extra"),
    [
        ([], {}),
        (
            ["--second", "en", "--dicts", "FR-EN", "--freq", "FR Freq"],
            {"second": "en", "dicts": ["FR-EN"], "freq": ["FR Freq"]},
        ),
    ],
)
def test_profile_app_reads_no_runtime_config(tmp_path, monkeypatch, flags, extra):
    from saitenka.app.cli import create_app

    path = tmp_path / "overlay.toml"
    path.write_text('dicts = ["JP"]\nfreq = ["JPDB"]\npitch = ["NHK"]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(path))

    result = create_app()(
        ["profile", "add", "french", "--language", "fr", "--yes", *flags],
        exit_on_error=False,
        result_action="return_value",
    )

    assert result == 0
    assert tomllib.loads(path.read_text())["profiles"] == {
        "french": {
            "language": "fr",
            "tokenizer": "latin",
            "dicts": [],
            "freq": [],
            "pitch": [],
            **extra,
        }
    }


def test_build_table_unknown_script_without_tokenizer_raises():
    with pytest.raises(ValueError, match="no default tokenizer"):
        build_profile_table(language="zh")


def test_build_table_explicit_tokenizer_for_unknown_script():
    assert build_profile_table(language="zh", tokenizer="latin")["tokenizer"] == "latin"


def test_add_proposal_rejects_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        add_proposal("", {"language": "fr"})


def test_add_proposal_rejects_the_reserved_default_name():
    with pytest.raises(ValueError, match="reserved"):
        add_proposal("default", {"language": "fr"})


@pytest.mark.parametrize(("name", "expected"), [("french", "french"), (None, "")])
def test_use_proposal_default_clears_the_selector(tmp_path, monkeypatch, name, expected):
    from saitenka.app.cli import create_app

    path = tmp_path / "overlay.toml"
    path.write_text('active_profile = "german"\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(path))

    result = create_app()(
        ["profile", "use", *([name] if name else []), "--yes"],
        exit_on_error=False,
        result_action="return_value",
    )

    assert result == 0
    assert tomllib.loads(path.read_text())["active_profile"] == expected


@pytest.mark.parametrize("active", ["french", "german"])
def test_remove_paths_also_clears_a_dangling_active_selector(tmp_path, monkeypatch, active):
    from saitenka.app.cli import create_app

    path = tmp_path / "overlay.toml"
    path.write_text(f'active_profile = "{active}"\n[profiles.french]\n[profiles.german]\n')
    monkeypatch.setenv("SAITENKA_CONFIG", str(path))

    result = create_app()(
        ["profile", "remove", "french", "--yes"],
        exit_on_error=False,
        result_action="return_value",
    )

    assert result == 0
    assert tomllib.loads(path.read_text()) == {
        "profiles": {"german": {}},
        **({"active_profile": "german"} if active == "german" else {}),
    }


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [({"profiles": {"german": {}, "french": {}}}, ["french", "german"]), ({}, [])],
)
def test_profile_names_sorted(cfg, expected):
    assert profile_names(cfg) == expected


def test_render_list_marks_the_active_profile():
    cfg = {"active_profile": "french", "profiles": {"french": {"language": "fr"}}}
    lines = render_list(cfg)
    assert any(
        line.startswith("* french") and "fr→en" in line and "[latin]" in line for line in lines
    )
    assert any(line.startswith("  default") for line in lines)


def test_render_show_reports_resolved_identity_and_scoped_dicts():
    cfg = {"profiles": {"french": {"language": "fr", "dicts": ["FR-EN"]}}}
    out = "\n".join(render_show(cfg, "french"))
    assert "profile:   french" in out
    assert "tokenizer: latin" in out
    assert "FR-EN" in out
