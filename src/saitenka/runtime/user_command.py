"""`Owner.SESSION`'s command intake: mpv reported a keypress or a script message, so run it.

Holds nothing, like the episode boundary. A command is an arrival, not a fact to accumulate — the
binding table it resolves against is the session's, and whether the session is in a state to run
one is a read of the connection feature. A slice's features do not read each other, so that read
belongs to the performer, which already has it.

What this feature is for, then, is the ownership: a command routed to `Owner.SESSION` is a command
the reactor decided to run, in envelope order with everything else the session heard.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.runtime.effects import RunUserCommand
from saitenka.runtime.events import UserCommand
from saitenka.runtime.state import ReduceResult

if TYPE_CHECKING:
    from saitenka.runtime.events import RuntimeEvent

#: The feature's key inside `Owner.SESSION`'s slice.
COMMAND_FEATURE = "user-command"


@dataclass(frozen=True, slots=True)
class CommandIntake:
    """Deliberately empty — see the module docstring."""


def reduce_user_command(state: object, event: RuntimeEvent, /) -> ReduceResult:
    """`FeatureReducer` for command arrival.

    The slice broadcasts, so this sees every `Owner.SESSION` event and answers with the state it
    was given for all but one of them.
    """
    assert isinstance(state, CommandIntake)
    if not isinstance(event, UserCommand):
        return ReduceResult(state)
    return ReduceResult(state, effects=(RunUserCommand(event),))
