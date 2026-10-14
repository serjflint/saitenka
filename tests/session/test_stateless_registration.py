from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from saitenka.app.session.routes import STATELESS_COMMANDS, stateless_features
from saitenka.app.session.stateless import StatelessCommandGraph, StatelessCommandRegistration

ROOT = Path(__file__).resolve().parents[2]
INTENTS = ROOT / "src/saitenka/app"


def _policy_types() -> set[type]:
    """Every typed stateless reducer under the application layer."""
    found: set[type] = set()
    for path in sorted(INTENTS.rglob("*intents.py")):
        relative = path.relative_to(ROOT / "src").with_suffix("")
        module = importlib.import_module(".".join(relative.parts))
        reduce = getattr(module, "reduce", None)
        if not callable(reduce):
            continue
        first = next(iter(inspect.signature(reduce).parameters))
        command_type = get_type_hints(reduce)[first]
        if isinstance(command_type, type):
            found.add(command_type)
    return found


class _Adapter:
    def inputs(self) -> object:
        return object()

    def apply(self, effect: object, /) -> None:
        pass


def _bindings():
    adapter = _Adapter()
    return stateless_features(
        adapter,  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
        adapter,  # type: ignore[arg-type]
    )


def test_every_stateless_policy_is_registered_or_named_as_residue() -> None:
    """Every discovered policy and every declared command must be wired."""
    registered = {binding.command_type for binding in _bindings()}

    assert _policy_types() == registered
    expected = {(command_type, command) for command_type in registered for command in command_type}
    assert {(type(row.command), row.command) for row in STATELESS_COMMANDS} == expected


def test_the_command_graph_refuses_a_command_no_feature_owns() -> None:
    """The negative control: registration is a real key, not a lookup that falls through."""
    with pytest.raises(KeyError, match="no stateless feature owns"):
        StatelessCommandGraph(_bindings(), STATELESS_COMMANDS).run("toggle-sidebar")


def test_the_command_graph_rejects_duplicate_command_types() -> None:
    binding = _bindings()[0]
    with pytest.raises(ValueError, match="already registered"):
        StatelessCommandGraph((binding, binding), ())


def test_the_command_graph_rejects_an_installed_policy_without_a_message() -> None:
    missing = _bindings()[0].command_type
    incomplete = tuple(row for row in STATELESS_COMMANDS if not isinstance(row.command, missing))

    with pytest.raises(ValueError, match="have no script messages"):
        StatelessCommandGraph(_bindings(), incomplete)


def test_the_command_graph_rejects_duplicate_messages() -> None:
    duplicate = StatelessCommandRegistration(
        STATELESS_COMMANDS[0].message,
        STATELESS_COMMANDS[1].command,
    )

    with pytest.raises(ValueError, match="already registered"):
        StatelessCommandGraph(_bindings(), (*STATELESS_COMMANDS, duplicate))
