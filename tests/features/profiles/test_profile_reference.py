"""Language/profile configuration input and output reference records."""

import json
import re
from pathlib import Path

import pytest

from saitenka.app.command_defaults import resolve_mine_model
from saitenka.app.launch.run import _resolvemine_target, defaultmine_target
from saitenka.app.mining_config import mine_config_from
from saitenka.app.profiles import (
    configured_profiles,
    primary_font_for,
    resolve_launch_identity,
    resolve_profile,
    scope_config,
    validate_language_code,
)

DATA = json.loads((Path(__file__).parents[2] / "fixtures/config_reference.json").read_text())


def _profile_value(profile):
    return {
        "name": profile.name,
        "main_language": profile.langs.main,
        "second_language": profile.langs.second,
        "tokenizer": profile.tokenizer,
        "slang": profile.slang,
    }


@pytest.mark.parametrize(
    "record", [r for r in DATA["resolve"] if "error" not in r["expected"]], ids=lambda r: r["id"]
)
def test_profile_resolution_reference(record):
    v = record["input"]
    assert (
        _profile_value(resolve_profile(v["config"], override=v.get("override")))
        == record["expected"]
    )


@pytest.mark.parametrize(
    "record", [r for r in DATA["resolve"] if "error" in r["expected"]], ids=lambda r: r["id"]
)
def test_invalid_profile_configuration_is_rejected(record):
    v = record["input"]
    with pytest.raises(ValueError, match=re.escape(record["expected"]["error"])):
        resolve_profile(v["config"], override=v.get("override"))


@pytest.mark.parametrize("record", DATA["validate-language"], ids=lambda r: r["id"])
def test_invalid_language_codes_are_rejected(record):
    with pytest.raises(ValueError, match=re.escape(record["expected"]["error"])):
        validate_language_code(record["input"]["language"])


@pytest.mark.parametrize("record", DATA["scope"], ids=lambda r: r["id"])
def test_profile_scoping_reference(record):
    v = record["input"]
    assert scope_config(v["config"], override=v.get("override")) == record["expected"]


@pytest.mark.parametrize("record", DATA["configured"], ids=lambda r: r["id"])
def test_configured_profile_order(record):
    assert [_profile_value(p) for p in configured_profiles(record["input"]["config"])] == record[
        "expected"
    ]


@pytest.mark.parametrize("record", DATA["launch"], ids=lambda r: r["id"])
def test_profile_launch_language_and_resources(record):
    v = record["input"]
    result = resolve_launch_identity(
        v["config"], profile_override=v.get("override"), slang=v["slang"]
    )
    assert {
        "profile": _profile_value(result.profile),
        "language": result.language,
        "slang": result.slang,
        "config": result.cfg,
    } == record["expected"]


@pytest.mark.parametrize("record", DATA["primary-font"], ids=lambda r: r["id"])
def test_profile_primary_font_reference(record):
    assert primary_font_for(record["input"]["language"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["mine-config"], ids=lambda r: r["id"])
def test_profile_scoped_mining_fields(record):
    v = record["input"]
    cfg = scope_config(v["config"], override=v.get("override"))
    mine = mine_config_from(cfg.get("mine", {}))
    assert {"deck": mine.deck, "model": mine.model, "fields": mine.fields} == record["expected"]


@pytest.mark.parametrize("record", DATA["mine-target"], ids=lambda r: r["id"])
def test_default_mining_target_reference(record):
    assert list(defaultmine_target(record["input"]["mine"])) == record["expected"]


@pytest.mark.parametrize("record", DATA["run-mine-target"], ids=lambda r: r["id"])
def test_profile_mining_target_precedence(record):
    v = record["input"]
    identity = resolve_launch_identity(
        v["config"], profile_override=v.get("override"), slang=v["slang"]
    )
    assert (
        list(_resolvemine_target(identity.cfg, v.get("mine_deck"), v.get("mine_model")))
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["mine-model"], ids=lambda r: r["id"])
def test_model_name_overrides_preset(record):
    assert resolve_mine_model(record["input"]) == record["expected"]
