"""Reading-profile command admission stays pure and feature-owned."""

from saitenka.app.features.profiles.profile_controller import next_profile_index


def test_cycling_moves_to_the_next_configured_profile() -> None:
    assert next_profile_index(profile_count=3, profile_index=1) == 2


def test_cycling_wraps_at_the_end() -> None:
    assert next_profile_index(profile_count=3, profile_index=2) == 0


def test_a_single_profile_session_is_inert() -> None:
    assert next_profile_index(profile_count=1, profile_index=0) is None


def test_the_decision_is_which_profile_not_whether_it_resolves() -> None:
    assert next_profile_index(profile_count=2, profile_index=0) == 1
