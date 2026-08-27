import importlib.util
import sys
from pathlib import Path

import pytest

_CHECK = Path(__file__).resolve().parent.parent / "tools" / "tooltip_ownership_check.py"


def _module():
    spec = importlib.util.spec_from_file_location("_tooltip_ownership_check", _CHECK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ownership = _module()


def _rules(source: str, relative: str) -> set[str]:
    path = ownership.APP / relative
    return {finding.rule for finding in ownership.inspect_source(source, path)}


def test_current_tooltip_ownership_tree_is_clean():
    assert ownership.inspect_tree() == []


@pytest.mark.parametrize("attribute", ["tip", "interaction"])
def test_session_cannot_reintroduce_tooltip_state_projections(attribute: str):
    rules = _rules(
        f"def drift(self):\n    return self.{attribute}\n",
        "session/controller.py",
    )

    assert "legacy-session-field" in rules


@pytest.mark.parametrize(
    "name",
    [
        "state",
        "hover_store",
        "word_store",
        "work_view",
        "metadata",
        "metadata_submitter",
        "engaged",
        "engaged_submitter",
        "render_ahead",
        "render_ahead_submitter",
        "selected",
        "pause_enabled",
    ],
)
def test_tooltip_owner_cannot_republish_mutable_state(name: str):
    rules = _rules(
        f"class TooltipController:\n    def {name}(self):\n        return self._state\n",
        "features/tooltip/tooltip_controller.py",
    )

    assert "owner-projection" in rules


def test_tooltip_owner_cannot_republish_state_under_a_new_name():
    rules = _rules(
        "class TooltipController:\n    def raw_state(self):\n        return self._state\n",
        "features/tooltip/tooltip_controller.py",
    )

    assert "owner-state-projection" in rules


def test_tooltip_owner_cannot_republish_a_nested_state_member():
    rules = _rules(
        "class TooltipController:\n    def raw_view(self):\n        return self._state.view\n",
        "features/tooltip/tooltip_controller.py",
    )

    assert "owner-state-projection" in rules


@pytest.mark.parametrize(
    "body",
    [
        "state = self._state\n        return state",
        "return (self._state,)",
        "state = self._state\n        return tuple((state,))",
    ],
)
def test_tooltip_owner_cannot_republish_wrapped_or_aliased_state(body: str):
    rules = _rules(
        f"class TooltipController:\n    def raw_state(self):\n        {body}\n",
        "features/tooltip/tooltip_controller.py",
    )

    assert "owner-state-projection" in rules


@pytest.mark.parametrize(
    "body",
    [
        "return owner.surface_state()",
        "surface = owner.surface_state\n    return surface()",
    ],
)
def test_feature_cannot_take_the_mutable_surface_state(body: str):
    rules = _rules(
        f"def drift(owner: TooltipController):\n    {body}\n",
        "features/mining/mine_adapter.py",
    )

    assert "owner-raw-boundary-outside-tooltip" in rules


def test_feature_cannot_hide_the_owner_type_behind_an_alias():
    rules = _rules(
        "TC = TooltipController\n"
        "def drift(owner: TC):\n"
        "    surface = owner.surface_state\n"
        "    return surface()\n",
        "features/mining/mine_adapter.py",
    )

    assert "owner-raw-boundary-outside-tooltip" in rules


@pytest.mark.parametrize(
    "declaration",
    [
        "from saitenka.app.features.tooltip.tooltip_controller import TooltipController as TC",
        "type TC = TooltipController",
    ],
)
def test_feature_cannot_hide_the_owner_type_behind_import_or_type_alias(declaration: str):
    rules = _rules(
        f"{declaration}\ndef drift(owner: TC):\n    return owner.surface_state()\n",
        "features/mining/mine_adapter.py",
    )

    assert "owner-raw-boundary-outside-tooltip" in rules


def test_tooltip_policy_module_cannot_take_mutable_surface_state():
    rules = _rules(
        "def drift(owner: TooltipController):\n    return owner.surface_state()\n",
        "features/tooltip/hover_adapter.py",
    )

    assert "owner-raw-boundary-outside-tooltip" in rules


@pytest.mark.parametrize("name", ["tip_ports", "panel_ports"])
def test_session_cannot_republish_raw_tooltip_ports(name: str):
    rules = _rules(
        f"class SessionController:\n    def {name}(self):\n        return object()\n",
        "session/controller.py",
    )

    assert "session-tooltip-port" in rules


@pytest.mark.parametrize(
    "body",
    [
        "return self._tip_ports",
        "ports = self._panel_ports\n        return ports",
        "return tuple((self._tip_ports,))",
    ],
)
def test_session_cannot_republish_private_tooltip_ports_under_a_new_name(body: str):
    rules = _rules(
        f"class SessionController:\n    def tooltip_capabilities(self):\n        {body}\n",
        "session/controller.py",
    )

    assert "session-tooltip-port" in rules


def test_non_physical_session_method_cannot_take_mutable_tooltip_state():
    rules = _rules(
        "class SessionController:\n"
        "    def decide_business_policy(self):\n"
        "        return self.tooltip_controller.surface_state()\n",
        "session/controller.py",
    )

    assert "owner-raw-boundary-outside-physical-method" in rules


@pytest.mark.parametrize(
    "attribute",
    [
        "prefetch",
        "prefetch_workers",
        "prefetch_lookahead",
        "head_prefetch_lookahead",
        "_mask_atlas_startup",
        "render_cache",
    ],
)
def test_preparation_state_cannot_return_to_the_session_shell(attribute: str):
    rules = _rules(
        f"def drift(self):\n    self.{attribute} = object()\n",
        "session/controller.py",
    )

    assert "legacy-session-field" in rules


def test_preparation_controllers_can_only_be_built_by_session_assembly():
    rules = _rules(
        "def drift():\n    return TooltipPreparationController()\n",
        "features/tooltip/tooltip.py",
    )

    assert "preparation-constructor" in rules


def test_aliased_preparation_construction_cannot_escape_the_assembly():
    rules = _rules(
        "from saitenka.app.features.tooltip.preparation import "
        "TooltipPreparationController as Preparation\n"
        "def drift():\n    return Preparation()\n",
        "features/tooltip/tooltip.py",
    )

    assert "preparation-constructor" in rules


def test_prewarm_cannot_reconstruct_a_full_session():
    rules = _rules(
        "from saitenka.app.session.controller import SessionController as Reader\n"
        "def drift():\n    return Reader()\n",
        "prewarm.py",
    )

    assert "full-session-prewarm" in rules


def test_prewarm_cannot_reconstruct_a_session_through_a_module_alias():
    rules = _rules(
        "import saitenka.app.session.controller as controller\n"
        "def drift():\n    return controller.SessionController()\n",
        "prewarm.py",
    )

    assert "full-session-prewarm" in rules


@pytest.mark.parametrize(
    "expression",
    [
        "self.tooltip_preparation.close_prefetch",
        "self.tooltip_preparation.close_mask_activation",
        "self.tooltip_preparation.cache.uninstall_mask_atlas",
    ],
)
def test_preparation_close_detail_cannot_return_to_the_session_shell(expression: str):
    rules = _rules(
        f"def drift(self):\n    return {expression}\n",
        "session/controller.py",
    )

    assert "preparation-close-detail" in rules


@pytest.mark.parametrize(
    "body",
    [
        "preparation = self.tooltip_preparation\n    return preparation.close_prefetch",
        """preparation = self.tooltip_preparation
    cache = preparation.cache
    return cache.uninstall_mask_atlas""",
    ],
)
def test_preparation_close_alias_cannot_return_to_the_session_shell(body: str):
    rules = _rules(
        f"def drift(self):\n    {body}\n",
        "session/controller.py",
    )

    assert "preparation-close-detail" in rules


def test_preparation_close_cannot_cross_a_typed_helper_parameter():
    rules = _rules(
        "def close(preparation: TooltipPreparationController):\n"
        "    return preparation.close_prefetch\n\n"
        "def drift(self):\n"
        "    return close(self.tooltip_preparation)\n",
        "session/controller.py",
    )

    assert "preparation-close-detail" in rules
