"""WP4.2: subtitle source/track/role selection decided by pure policy, not by IPC order."""

from __future__ import annotations

import pytest
from saitenka_tokenize.languages import MAIN_LANG, SECOND_LANG

from saitenka.app.subtitle_selection import (
    FetchAction,
    SubtitleStartup,
    SubtitleTracks,
    discover,
    fetch_action,
    initial,
    language_name,
    primary_role,
    selects_background_japanese,
    toggle_target,
)


def track(sid: int, lang: str | None = None, **extra: object) -> dict:
    return {"id": sid, "lang": lang, "type": "sub", **extra}


# --- discovery ---------------------------------------------------------------------------------


def test_tagged_tracks_are_classified_by_language() -> None:
    tracks = [track(1, "eng"), track(2, "jpn")]

    assert discover(tracks, "ja,jpn,jp") == SubtitleTracks(jp_sid=2, en_sid=1)


def test_an_untagged_selected_track_becomes_the_target() -> None:
    tracks = [track(1, "eng"), track(2, None, selected=True)]

    assert discover(tracks, "ja,jpn,jp") == SubtitleTracks(jp_sid=2, en_sid=1)


def test_a_lone_untagged_track_fills_only_the_target_role() -> None:
    assert discover([track(7)], "ja,jpn,jp") == SubtitleTracks(jp_sid=7, en_sid=None)


def test_no_tracks_selects_nothing() -> None:
    assert initial([], "ja,jpn,jp") == SubtitleStartup(SubtitleTracks(None, None), None)


def test_an_unrelated_tagged_track_does_not_claim_either_configured_role() -> None:
    tracks = [track(7, "eng", selected=True)]

    assert initial(tracks, "fr", "de") == SubtitleStartup(SubtitleTracks(None, None), None)


def test_startup_prefers_the_target_language_then_tagged_english() -> None:
    assert initial([track(1, "eng"), track(2, "jpn")], "ja,jpn,jp").active == MAIN_LANG
    assert initial([track(1, "eng")], "ja,jpn,jp").active == SECOND_LANG


# --- toggle ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("active_sid", "language", "expected_target", "expected_sid"),
    [
        (2, MAIN_LANG, SECOND_LANG, 1),
        (1, SECOND_LANG, MAIN_LANG, 2),
        (None, MAIN_LANG, MAIN_LANG, 2),
        (None, SECOND_LANG, SECOND_LANG, 1),
    ],
)
def test_toggle_switches_away_from_whatever_is_on_screen(
    active_sid: object, language: str, expected_target: str, expected_sid: int
) -> None:
    decision = toggle_target(
        SubtitleTracks(jp_sid=2, en_sid=1), active_sid=active_sid, language=language
    )

    assert (decision.target, decision.sid) == (expected_target, expected_sid)
    assert decision.available


def test_toggle_reports_an_unavailable_target_instead_of_guessing() -> None:
    decision = toggle_target(
        SubtitleTracks(jp_sid=None, en_sid=None), active_sid=None, language=MAIN_LANG
    )

    assert decision.available is False
    assert decision.sid is None


# --- role classification -----------------------------------------------------------------------


def test_a_known_sid_keeps_its_established_role() -> None:
    tracks = SubtitleTracks(jp_sid=2, en_sid=1)

    assert primary_role(2, tracks, track_lang="eng", sample="hello") == MAIN_LANG
    assert primary_role(1, tracks, track_lang="jpn", sample="猫") == SECOND_LANG


def test_an_unknown_tagged_track_is_classified_by_its_tag() -> None:
    tracks = SubtitleTracks(jp_sid=None, en_sid=None)

    assert primary_role(9, tracks, track_lang="jpn", sample="hello") == MAIN_LANG
    assert primary_role(9, tracks, track_lang="eng", sample="猫を見る") == SECOND_LANG


def test_an_unknown_track_uses_the_configured_profile_languages() -> None:
    tracks = SubtitleTracks(jp_sid=6, en_sid=8)

    role = primary_role(
        9,
        tracks,
        track_lang="fra",
        sample="Bonjour",
        primary_slang="fr",
        second_slang="de",
    )

    assert role == MAIN_LANG


def test_an_untagged_track_is_classified_by_its_content() -> None:
    """A missing tag wildcard-matches English, so only the cue text can decide."""
    tracks = SubtitleTracks(jp_sid=None, en_sid=None)

    assert primary_role(9, tracks, track_lang=None, sample="猫を見る") == MAIN_LANG
    assert primary_role(9, tracks, track_lang=None, sample="just english here") == SECOND_LANG


def test_an_untagged_track_with_no_sample_defaults_to_the_target() -> None:
    tracks = SubtitleTracks(jp_sid=None, en_sid=None)

    assert primary_role(9, tracks, track_lang=None, sample="") == MAIN_LANG


def test_language_name_falls_back_to_the_raw_tag() -> None:
    assert language_name("jpn") == "Japanese"
    assert language_name("eng") == "English"
    assert language_name("kor") == "kor"
    assert language_name(None) == "unknown language"


# --- fetch arrival policy ------------------------------------------------------------------------


def test_a_failed_fetch_never_touches_the_selection() -> None:
    action = fetch_action(path_available=False, force_select=True, replace=True, language=MAIN_LANG)

    assert action is FetchAction.REPORT_FAILURE


def test_an_explicit_picker_choice_selects_from_any_language() -> None:
    for language in (MAIN_LANG, SECOND_LANG):
        action = fetch_action(
            path_available=True, force_select=True, replace=False, language=language
        )
        assert action is FetchAction.REPLACE


def test_a_retry_swaps_only_while_already_on_the_target_language() -> None:
    on_target = fetch_action(
        path_available=True, force_select=False, replace=True, language=MAIN_LANG
    )
    on_secondary = fetch_action(
        path_available=True, force_select=False, replace=True, language=SECOND_LANG
    )

    assert on_target is FetchAction.REPLACE
    assert on_secondary is FetchAction.BACKGROUND_ADD


def test_a_background_arrival_never_replaces() -> None:
    action = fetch_action(
        path_available=True, force_select=False, replace=False, language=MAIN_LANG
    )

    assert action is FetchAction.BACKGROUND_ADD


def test_a_background_arrival_auto_selects_only_for_an_untouched_trackless_session() -> None:
    assert selects_background_japanese(
        select_if_unchanged=True, had_japanese=False, current_sid=1, initial_sid=1, jp_sid=3
    )
    # the user changed track while the fetch was in flight
    assert not selects_background_japanese(
        select_if_unchanged=True, had_japanese=False, current_sid=2, initial_sid=1, jp_sid=3
    )
    # a target track already existed
    assert not selects_background_japanese(
        select_if_unchanged=True, had_japanese=True, current_sid=1, initial_sid=1, jp_sid=3
    )
    # the caller did not opt into selection
    assert not selects_background_japanese(
        select_if_unchanged=False, had_japanese=False, current_sid=1, initial_sid=1, jp_sid=3
    )
