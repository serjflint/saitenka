"""Pure admission policy for reading-profile commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ProfileCommand(StrEnum):
    CYCLE = "cycle-profile"


@dataclass(frozen=True, slots=True)
class ProfileInputs:
    profile_count: int
    profile_index: int


@dataclass(frozen=True, slots=True)
class SwitchProfile:
    index: int


type ProfileEffect = SwitchProfile


def reduce(command: ProfileCommand, inputs: ProfileInputs) -> tuple[ProfileEffect, ...]:
    if command is not ProfileCommand.CYCLE:
        raise AssertionError(command)
    if inputs.profile_count <= 1:
        return ()
    return (SwitchProfile((inputs.profile_index + 1) % inputs.profile_count),)
