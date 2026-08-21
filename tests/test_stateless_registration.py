from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from saitenka.app.session_routes import stateless_features

ROOT = Path(__file__).resolve().parent.parent
INTENTS = ROOT / "src/saitenka/app"

#: Policies whose adapter still lives on `Reader`. Closed by being written down: a new
#: `*_intents.py` is not on this list, so it fails until it registers. Only shrinks —
#: `vibe/stateless-seam-plan.md` says what each is blocked on (the width of its host port).
NOT_YET_REGISTERED = frozenset({"hover", "mine", "session", "subtitle"})


def _policies() -> set[str]:
    """Every stateless policy module: an `app/*_intents.py` exposing a `reduce`."""
    found = set()
    for path in sorted(INTENTS.glob("*_intents.py")):
        module = importlib.import_module(f"saitenka.app.{path.stem}")
        if callable(getattr(module, "reduce", None)):
            found.add(path.stem.removesuffix("_intents"))
    return found


class _Host:
    """Not a `Reader`: the point of the ports is that a registration needs no live session."""

    def __getattr__(self, name: str) -> object:
        return None


def test_every_stateless_policy_is_registered_or_named_as_residue() -> None:
    """The seam only exists if the next feature has to use it.

    `docs/contributing/runtime.md` declared the effect-interpreter invariant with nothing
    implementing it, and that is how a declared architecture and the true one come apart. This is
    the implementation: an unregistered policy is a failure unless it is on a list that only
    shrinks.
    """
    registered = {
        entry[1].__class__.__name__.removesuffix("Adapter").lower()
        for entry in stateless_features(_Host()).values()  # type: ignore[arg-type]
    }

    assert _policies() - registered - NOT_YET_REGISTERED == set()


def test_the_router_refuses_a_command_no_feature_owns() -> None:
    """The negative control: registration is a real key, not a lookup that falls through."""
    from saitenka.app.stateless import StatelessRouter

    with pytest.raises(KeyError, match="no stateless feature owns"):
        StatelessRouter({}).run("toggle-sidebar")
