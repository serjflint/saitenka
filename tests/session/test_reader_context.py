"""Subtitle navigation state defaults."""

from saitenka.app.features.subtitle.navigation_state import NavigationState


def test_navigation_state_defaults_are_the_no_source_state():
    ctx = NavigationState()
    assert (
        ctx.sub_index,
        ctx.nav_idx,
        ctx.sub_settle.open,
        ctx.nav_prev_text,
        ctx.nav_provisional_cue_counted,
    ) == (
        None,
        -1,
        False,
        "",
        False,
    )
