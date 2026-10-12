"""Reading-profile command admission stays pure and feature-owned."""

from saitenka.app.features.profiles.profile_controller import next_profile_index


def test_a_single_profile_session_is_inert() -> None:
    assert next_profile_index(profile_count=1, profile_index=0) is None
