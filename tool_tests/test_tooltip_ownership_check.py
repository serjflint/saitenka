"""Planted controls for the tooltip ownership census."""

from __future__ import annotations

from pathlib import Path

import tooltip_ownership_check


def _rules(source: str, path: str = "src/saitenka/app/foreign.py") -> set[str]:
    return {finding.rule for finding in tooltip_ownership_check.inspect_source(source, Path(path))}


def test_current_production_tree_routes_tooltip_state_through_its_owner() -> None:
    assert tooltip_ownership_check.inspect_tree() == []


def test_tooltip_state_and_store_construction_outside_the_owner_is_rejected() -> None:
    assert _rules(
        """
def build(ipc, lock):
    state = TooltipState(panel_cache_max=4, cache_lock=lock)
    return HoverStore(ipc), TipNavStore(ipc), PulseStore(ipc), HoverPauseStore(ipc), HoveredWordStore(ipc)
"""
    ) == {"owned-constructor"}


def test_direct_private_owner_state_writes_outside_the_owner_are_rejected() -> None:
    assert _rules(
        """
def replace(host):
    host.tooltip_controller._selected = 2
"""
    ) == {"owned-state-write"}


def test_private_owner_state_writes_through_an_alias_are_rejected() -> None:
    assert _rules(
        """
def replace(host):
    owner = host.tooltip_controller
    owner._selected = 2
    del owner._word_store
"""
    ) == {"owned-state-write"}


def test_private_owner_state_writes_through_a_typed_parameter_are_rejected() -> None:
    assert _rules(
        """
def replace(owner: TooltipController, delays):
    owner._pause_enabled = False
    owner._delays = delays
"""
    ) == {"owned-state-write"}


def test_unrelated_private_state_names_remain_available_to_other_owners() -> None:
    assert (
        _rules(
            """
class PickerController:
    def configure(self, delays):
        self._selected = 2
        self._delays = delays
        self._flash_seconds = 0.5

def configure(owner: PickerController):
    alias = owner
    alias._selected = 3
"""
        )
        == set()
    )


def test_retired_session_fields_are_rejected_on_read_or_write() -> None:
    assert _rules(
        """
class SessionController:
    def configure(self):
        self.hover = -1
        return self.pause_on_tooltip, self._hover_store
""",
        "src/saitenka/app/session/controller.py",
    ) == {"legacy-session-field"}


def test_session_cannot_write_tooltip_paint_or_cache_state_directly() -> None:
    assert _rules(
        """
class SessionController:
    def clear(self, key, panel):
        self.tip.view.rect = None
        self.tip.tip_tok = None
        self.tip.panel_cache.setdefault(key, panel)
""",
        "src/saitenka/app/session/controller.py",
    ) == {"legacy-session-field", "tooltip-cache-write", "tooltip-state-write"}


def test_keybinding_fact_has_one_writer() -> None:
    assert _rules("view.tip_keys_bound = True") == {"keybinding-state-write"}


def test_owner_and_port_driven_paint_helpers_remain_allowed() -> None:
    assert not tooltip_ownership_check.inspect_source(
        """
def own(self, ipc):
    self._selected = -1
    self._hover_store = HoverStore(ipc)
    self._state.tip_keys_bound = True
""",
        Path("src/saitenka/app/features/tooltip/tooltip_controller.py"),
    )
    assert not tooltip_ownership_check.inspect_source(
        """
def paint(ports, panel):
    ports.tip.view.state = panel
    ports.tip.view.rect = (0, 0, 1, 1)
""",
        Path("src/saitenka/app/features/tooltip/tooltip.py"),
    )


def test_nested_app_modules_are_scanned(tmp_path) -> None:
    nested = tmp_path / "commands"
    nested.mkdir()
    path = nested / "foreign.py"
    path.write_text("store = HoverStore(ipc)\n", encoding="utf-8")

    findings = tooltip_ownership_check.inspect_tree(tmp_path)

    assert [(finding.path, finding.rule) for finding in findings] == [(path, "owned-constructor")]
