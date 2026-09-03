"""``saitenka profile`` value plumbing (#254 W4) — the pure proposal builders + renderers.

The cyclopts shell (add/use/remove/list/show) is ``pragma: no cover``; these assert the functions it
calls, against constructed cfg dicts. Observable output only: the proposal/remove-path dicts a write
would apply, and the rendered lines.
"""

from __future__ import annotations

import pytest

from saitenka.app.features.profiles.profile_cli import (
    add_proposal,
    build_profile_table,
    profile_app,
    profile_names,
    remove_paths,
    render_list,
    render_show,
    use_proposal,
)


def test_profile_app_reads_no_runtime_config():
    # Regression: the profile sub-app must NOT inherit the root app's Toml config layer, or its
    # --dicts/--freq/--pitch params get pre-filled from the top-level overlay.toml — the bug where a
    # French profile silently borrowed the Japanese dicts / NHK pitch as param defaults.
    assert profile_app.config == ()


def test_build_table_always_scopes_all_lists_empty_when_unspecified():
    # A profile never inherits the top-level dicts — unspecified dict/freq/pitch are written EMPTY so a
    # French profile can't silently borrow the Japanese dictionaries / NHK pitch.
    assert build_profile_table(language="fr") == {
        "language": "fr",
        "tokenizer": "latin",
        "dicts": [],
        "freq": [],
        "pitch": [],
    }


def test_build_table_scopes_dicts_and_second_when_given():
    table = build_profile_table(
        language="fr", second="en", dicts=["FR-EN"], freq=["FR Freq"], pitch=[]
    )
    assert table == {
        "language": "fr",
        "tokenizer": "latin",
        "second": "en",
        "dicts": ["FR-EN"],
        "freq": ["FR Freq"],
        "pitch": [],  # explicit empty — scopes to nothing, never inherits the top-level pitch
    }


def test_build_table_unknown_script_without_tokenizer_raises():
    with pytest.raises(ValueError, match="no default tokenizer"):
        build_profile_table(language="zh")


def test_build_table_explicit_tokenizer_for_unknown_script():
    assert build_profile_table(language="zh", tokenizer="latin")["tokenizer"] == "latin"


def test_add_proposal_nests_under_the_name():
    assert add_proposal("french", {"language": "fr"}) == {
        "profiles": {"french": {"language": "fr"}}
    }


def test_add_proposal_rejects_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        add_proposal("", {"language": "fr"})


def test_add_proposal_rejects_the_reserved_default_name():
    with pytest.raises(ValueError, match="reserved"):
        add_proposal("default", {"language": "fr"})


def test_use_proposal_default_clears_the_selector():
    assert use_proposal("french") == {"active_profile": "french"}
    assert use_proposal(None) == {"active_profile": ""}


def test_remove_paths_also_clears_a_dangling_active_selector():
    cfg = {"active_profile": "french", "profiles": {"french": {"language": "fr"}}}
    assert remove_paths(cfg, "french") == (("profiles", "french"), ("active_profile",))


def test_remove_paths_keeps_an_unrelated_active_selector():
    cfg = {"active_profile": "german", "profiles": {"french": {}, "german": {}}}
    assert remove_paths(cfg, "french") == (("profiles", "french"),)


def test_profile_names_sorted():
    assert profile_names({"profiles": {"french": {}, "german": {}}}) == ["french", "german"]
    assert profile_names({}) == []


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
