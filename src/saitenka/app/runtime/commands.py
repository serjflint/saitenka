"""Feature-owned script-message dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class CommandRouter:
    """A closed command table assembled once at reader construction.

    Duplicate claims are rejected so enabling a feature cannot silently replace another feature's
    shortcut. Handlers are already bound to their narrow owner; the router never receives a Reader.
    """

    def __init__(
        self,
        handlers: Mapping[str, Callable[[], None]] | None = None,
        *,
        cue_independent: frozenset[str] = frozenset(),
    ) -> None:
        self._handlers: dict[str, Callable[[], None]] = {}
        self._cue_independent = cue_independent
        for name, handler in (handlers or {}).items():
            self.register(name, handler)

    def register(self, name: str, handler: Callable[[], None]) -> None:
        if name in self._handlers:
            raise ValueError(f"script message already registered: {name}")
        self._handlers[name] = handler

    def dispatch(self, name: str) -> bool:
        handler = self._handlers.get(name)
        if handler is None:
            return False
        handler()
        return True

    def names(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def requires_cue(self, name: str) -> bool:
        return name in self._handlers and name not in self._cue_independent
