import importlib.util
import sys
from pathlib import Path

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


def test_preparation_state_cannot_return_to_the_session_shell():
    rules = _rules(
        "def drift(self):\n    self.prefetch_state = object()\n",
        "session/controller.py",
    )

    assert "legacy-session-field" in rules


def test_preparation_controllers_can_only_be_built_by_session_assembly():
    rules = _rules(
        "def drift():\n    return TooltipPreparationController()\n",
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
