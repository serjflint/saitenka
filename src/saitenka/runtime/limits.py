"""Closed resource policy for the session runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    mailbox_normal: int = 256
    mailbox_lifecycle: int = 8
    mailbox_terminal: int = 128
    mailbox_turn: int = 64
    internal_events_per_turn: int = 256
    effects_per_turn: int = 256
    outbound_mpv_commands: int = 64
    close_effects: int = 32
    close_deadline_ms: int = 5_000
    adapter_timeout_ms: int = 10_000
    reconnect_attempts: int = 3
    payload_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        values = tuple(getattr(self, field) for field in self.__dataclass_fields__)
        if any(value <= 0 for value in values):
            raise ValueError("runtime limits must be positive")


DEFAULT_RUNTIME_LIMITS = RuntimeLimits()
