"""The session runtime's bounded-work policy: one place, and every field has an enforcer.

Only bounds something actually reads live here. This file used to declare twelve and enforce none,
which is worse than no policy at all: `mailbox_terminal` said 128 while the mailbox ran at 64 and
nothing could notice, because a limit nobody applies cannot disagree with anything. The mailbox's
value won — it was the one in force. A field arrives here when its enforcer does, not before.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    mailbox_normal: int = 256
    mailbox_lifecycle: int = 8
    mailbox_terminal: int = 64
    mailbox_turn: int = 64

    def __post_init__(self) -> None:
        values = tuple(getattr(self, field) for field in self.__dataclass_fields__)
        if any(value <= 0 for value in values):
            raise ValueError("runtime limits must be positive")


DEFAULT_RUNTIME_LIMITS = RuntimeLimits()
