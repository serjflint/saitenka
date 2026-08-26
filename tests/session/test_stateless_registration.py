from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import get_type_hints

import pytest

from saitenka.app.session.routes import STATELESS_COMMANDS, stateless_features
from saitenka.app.session.stateless import StatelessCommandGraph, StatelessCommandRegistration

ROOT = Path(__file__).resolve().parents[2]
INTENTS = ROOT / "src/saitenka/app"


def _boundary_paths() -> tuple[Path, ...]:
    return tuple(sorted({*INTENTS.rglob("*adapter.py"), *INTENTS.rglob("*endpoint.py")}))


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
        adapter,  # type: ignore[arg-type]
    )


def test_every_stateless_policy_is_registered_or_named_as_residue() -> None:
    """The seam only exists if the next feature has to use it.

    `docs/contributing/runtime.md` declared the effect-interpreter invariant with nothing
    implementing it, and that is how a declared architecture and the true one come apart. This is
    the implementation: an unregistered policy is a failure unless it is on a list that only
    shrinks.
    """
    registered = {binding.command_type for binding in _bindings()}

    assert _policy_types() == registered


def test_the_router_refuses_a_command_no_feature_owns() -> None:
    """The negative control: registration is a real key, not a lookup that falls through."""
    from saitenka.app.session.stateless import StatelessRouter

    with pytest.raises(KeyError, match="no stateless feature owns"):
        StatelessRouter(()).run("toggle-sidebar")


def test_the_router_rejects_duplicate_command_types() -> None:
    from saitenka.app.session.stateless import StatelessRouter

    binding = _bindings()[0]
    with pytest.raises(ValueError, match="already registered"):
        StatelessRouter((binding, binding))


def test_the_command_graph_closes_every_installed_policy() -> None:
    graph = StatelessCommandGraph(_bindings(), STATELESS_COMMANDS)

    assert graph.command_types == {binding.command_type for binding in _bindings()}


def test_the_command_graph_rejects_an_installed_policy_without_a_message() -> None:
    with pytest.raises(ValueError, match="have no script messages"):
        StatelessCommandGraph(_bindings(), STATELESS_COMMANDS[:-1])


def test_the_command_graph_rejects_duplicate_messages() -> None:
    duplicate = StatelessCommandRegistration(
        STATELESS_COMMANDS[0].message,
        STATELESS_COMMANDS[1].command,
    )

    with pytest.raises(ValueError, match="already registered"):
        StatelessCommandGraph(_bindings(), (*STATELESS_COMMANDS, duplicate))


def test_stateless_boundaries_do_not_capture_the_session_or_replaceable_episode() -> None:
    forbidden: list[str] = []
    for path in _boundary_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            annotation = getattr(node, "annotation", None)
            if annotation is None:
                continue
            text = ast.unparse(annotation)
            if "SessionController" in text or "EpisodeContext" in text:
                forbidden.append(f"{path.relative_to(ROOT)}:{node.lineno} {text}")

    assert forbidden == []


def test_stateless_capabilities_do_not_hide_authority_in_opaque_callables() -> None:
    opaque: list[str] = []
    for path in _boundary_paths():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            text = ast.unparse(node.annotation)
            if "Callable[...," in text:
                opaque.append(f"{path.relative_to(ROOT)}:{node.lineno} {text}")

    assert opaque == []


def test_stateless_command_composition_has_no_deferred_session_reads() -> None:
    path = INTENTS / "session" / "controller.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    controller = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SessionController"
    )
    graph = next(
        node
        for node in controller.body
        if isinstance(node, ast.FunctionDef) and node.name == "_stateless_commands"
    )

    assert [node.lineno for node in ast.walk(graph) if isinstance(node, ast.Lambda)] == []
