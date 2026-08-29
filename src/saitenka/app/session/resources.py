"""Adapting a retirement *step* to the session-resource contract.

The runtime retires a resource by calling `close()`. Some duties are not an object with a lifetime
— finishing the session row, closing a store that may never have opened — and this is what lets
them register anyway, instead of the dispatcher growing a second way to retire something.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class ResourceRetirementError(RuntimeError):
    """A retirement sequence ran every participant but one or more failed."""

    def __init__(
        self,
        *,
        missing: tuple[str, ...],
        failed: tuple[tuple[str, BaseException], ...],
    ) -> None:
        self.missing = missing
        self.failed = failed
        details = [*(f"{name}: missing" for name in missing)]
        details.extend(f"{name}: {type(error).__name__}" for name, error in failed)
        super().__init__("session resources failed to retire: " + ", ".join(details))


@dataclass(frozen=True, slots=True)
class Starting:
    """One setup step, as a session participant.

    Deliberately not `Retiring` with a different name: `close()` meaning "start" would be a lie in
    the one place the effect vocabulary has to stay readable, so the setup half keeps its own verb.
    """

    begin: Callable[[], None]

    def start(self) -> None:
        self.begin()


@dataclass(frozen=True, slots=True)
class Performing:
    """One act on what the effect carries, as a session participant.

    A third verb because a third shape. `start()` and `close()` take nothing, which is right for an
    act whose subject is the session itself; an act *about* something has to be told what, and
    smuggling that through the registration would lose it the moment two arrive in one batch.
    """

    act: Callable[[object], None]

    def perform(self, effect: object) -> None:
        self.act(effect)


@dataclass(frozen=True, slots=True)
class Retiring:
    """One retirement step, wearing the resource contract.

    The callable is bound late on purpose: a lazily-opened store and a per-episode recorder are
    both absent when the session registers, so a resource holding the *object* would retire
    whatever existed at startup and leak whatever replaced it.
    """

    retire: Callable[[], None]

    def close(self) -> None:
        self.retire()
