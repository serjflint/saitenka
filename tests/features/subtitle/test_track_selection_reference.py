"""Track/language selection reference inputs and observable decisions."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from saitenka.app import subtitle_selection as selection

DATA = json.loads(
    (Path(__file__).parents[2] / "fixtures/subtitle_reference.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("record", DATA["discover"], ids=lambda r: r["id"])
def test_track_discovery_reference(record):
    v = record["input"]
    assert asdict(selection.discover(v["tracks"], v["primary"])) == record["expected"]


@pytest.mark.parametrize("record", DATA["initial"], ids=lambda r: r["id"])
def test_initial_track_reference(record):
    v = record["input"]
    assert (
        asdict(selection.initial(v["tracks"], v["primary"], v["secondary"])) == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["toggle"], ids=lambda r: r["id"])
def test_language_toggle_reference(record):
    v = record["input"]
    decision = selection.toggle_target(
        selection.SubtitleTracks(v["jp_sid"], v["en_sid"]),
        active_sid=v["active_sid"],
        language=v["language"],
    )
    assert asdict(decision) | {"available": decision.available} == record["expected"]


@pytest.mark.parametrize("record", DATA["role"], ids=lambda r: r["id"])
def test_primary_track_role_reference(record):
    v = record["input"]
    assert (
        selection.primary_role(
            v["sid"],
            selection.SubtitleTracks(v["jp_sid"], v["en_sid"]),
            track_lang=v["track_lang"],
            sample=v["sample"],
            primary_slang=v.get("primary", "ja,jpn,jp"),
            second_slang=v.get("secondary", "en"),
        )
        == record["expected"]
    )


@pytest.mark.parametrize("record", DATA["language-name"], ids=lambda r: r["id"])
def test_language_display_names(record):
    assert selection.language_name(record["input"]["language"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["fetch"], ids=lambda r: r["id"])
def test_fetch_decision_reference(record):
    assert selection.fetch_action(**record["input"]).value == record["expected"]


@pytest.mark.parametrize("record", DATA["background-select"], ids=lambda r: r["id"])
def test_background_selection_reference(record):
    assert selection.selects_background_japanese(**record["input"]) == record["expected"]


@pytest.mark.parametrize("record", DATA["matches-language"], ids=lambda r: r["id"])
def test_track_language_aliases(record):
    v = record["input"]
    assert selection.matches_target_language(v["tag"], v["target"]) == record["expected"]
