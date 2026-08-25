from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from saitenka.app.features.mining.mine_adapter import MineAdapter
from saitenka.app.features.profiles.profile_adapter import ProfileAdapter
from saitenka.app.features.tooltip.hover_adapter import HoverAdapter
from saitenka.app.session.adapter import SessionAdapter
from saitenka.app.session.interaction_adapter import InteractionAdapter
from saitenka.app.session.panel_adapter import PanelAdapter
from saitenka.app.session.routes import stateless_features
from saitenka.app.subtitle_adapter import SubtitleAdapter

ROOT = Path(__file__).resolve().parents[2]
INTENTS = ROOT / "src/saitenka/app"

#: Policies whose adapter still lives on `SessionController`. Closed by being written down: a new
#: `*_intents.py` is not on this list, so it fails until it registers. Only shrinks —
#: `vibe/stateless-seam-plan.md` says what each is blocked on (the width of its host port).
NOT_YET_REGISTERED: frozenset[str] = frozenset()


def _policies() -> set[str]:
    """Every stateless policy module: an `app/*_intents.py` exposing a `reduce`."""
    found = set()
    for path in sorted(INTENTS.glob("*_intents.py")):
        module = importlib.import_module(f"saitenka.app.{path.stem}")
        if callable(getattr(module, "reduce", None)):
            found.add(path.stem.removesuffix("_intents"))
    return found


class _Host:
    """Not a `SessionController`: the point of the ports is that a registration needs no live session."""

    def __getattr__(self, name: str) -> object:
        return None


def _bindings():
    host = _Host()
    return stateless_features(
        HoverAdapter(host),  # type: ignore[arg-type]
        MineAdapter(host),  # type: ignore[arg-type]
        PanelAdapter(host),  # type: ignore[arg-type]
        ProfileAdapter(host),  # type: ignore[arg-type]
        SessionAdapter(host),  # type: ignore[arg-type]
        SubtitleAdapter(host),  # type: ignore[arg-type]
        InteractionAdapter(host),  # type: ignore[arg-type]
    )


def test_every_stateless_policy_is_registered_or_named_as_residue() -> None:
    """The seam only exists if the next feature has to use it.

    `docs/contributing/runtime.md` declared the effect-interpreter invariant with nothing
    implementing it, and that is how a declared architecture and the true one come apart. This is
    the implementation: an unregistered policy is a failure unless it is on a list that only
    shrinks.
    """
    registered = {binding.feature for binding in _bindings()}

    assert _policies() - registered - NOT_YET_REGISTERED == set()


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
