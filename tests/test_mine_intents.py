"""WP5.3: mining eligibility is a pure function of the facts it reads."""

from __future__ import annotations

import pytest

from saitenka.app.intents import Announce
from saitenka.app.mine_intents import (
    MineCommand,
    MineEpisode,
    MineInputs,
    MineToken,
    reduce,
)

READY = MineInputs(configured=True, target=3)
_WORD_COMMANDS = [MineCommand.WORD, MineCommand.WORD_VIDEO]


def test_mining_a_word_defers_the_screenshot_mode_to_configuration() -> None:
    assert reduce(MineCommand.WORD, READY) == (MineToken(3, animated=None),)


def test_the_video_shortcut_forces_a_motion_screenshot() -> None:
    """`animated=True` overrides `[mine].animated_screenshot`; `None` is not the same answer as
    False, which is why the effect carries a tri-state rather than a bool."""
    assert reduce(MineCommand.WORD_VIDEO, READY) == (MineToken(3, animated=True),)


@pytest.mark.parametrize("command", _WORD_COMMANDS)
def test_no_word_under_the_cursor_says_so(command: MineCommand) -> None:
    assert reduce(command, MineInputs(configured=True, target=None)) == (
        Announce("no word to mine", "warn"),
    )


@pytest.mark.parametrize("command", _WORD_COMMANDS)
def test_an_unconfigured_session_decides_nothing(command: MineCommand) -> None:
    """Mining is optional. A user who never set Anki up should not be toasted for every stray key,
    so this stays quiet — the executor logs the reason, so it is not silent, just not shouted."""
    assert reduce(command, MineInputs()) == ()


def test_missing_configuration_outranks_a_missing_target() -> None:
    """Both are unmet at once when Anki is absent, and only one of them is worth telling the user
    about — announcing "no word to mine" to someone with no Anki names the wrong problem."""
    assert reduce(MineCommand.WORD, MineInputs(configured=False, target=None)) == ()


def test_bulk_mining_needs_no_cursor() -> None:
    """It reads the episode index rather than the hover, and answers for itself when the episode
    has nothing to mine — so gating it on a target here would refuse a request that is fine."""
    assert reduce(MineCommand.EPISODE, MineInputs()) == (MineEpisode(),)


def test_the_reducer_reads_its_inputs_without_mutating_them() -> None:
    given = MineInputs(configured=True, target=3)

    for command in MineCommand:
        reduce(command, given)

    assert given == MineInputs(configured=True, target=3)
