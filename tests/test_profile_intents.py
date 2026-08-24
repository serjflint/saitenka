"""Reading-profile command admission stays pure and feature-owned."""

from saitenka.app.profile_intents import (
    ProfileCommand,
    ProfileInputs,
    SwitchProfile,
    reduce,
)


def test_cycling_moves_to_the_next_configured_profile() -> None:
    assert reduce(ProfileCommand.CYCLE, ProfileInputs(profile_count=3, profile_index=1)) == (
        SwitchProfile(2),
    )


def test_cycling_wraps_at_the_end() -> None:
    assert reduce(ProfileCommand.CYCLE, ProfileInputs(profile_count=3, profile_index=2)) == (
        SwitchProfile(0),
    )


def test_a_single_profile_session_is_inert() -> None:
    assert reduce(ProfileCommand.CYCLE, ProfileInputs(profile_count=1, profile_index=0)) == ()


def test_the_decision_is_which_profile_not_whether_it_resolves() -> None:
    (effect,) = reduce(ProfileCommand.CYCLE, ProfileInputs(profile_count=2, profile_index=0))

    assert effect == SwitchProfile(1)
