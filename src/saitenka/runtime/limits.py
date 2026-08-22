"""The session runtime's bounded-work policy: one place, and every field has an enforcer.

Only bounds something actually reads live here. A declared-but-unenforced limit is worse than no
policy at all — nothing can notice it disagreeing with the value actually in force, because a limit
nobody applies cannot disagree with anything. A field arrives here when its enforcer does.
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
