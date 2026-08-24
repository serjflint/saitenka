"""Planted controls for the mining ownership census."""

from __future__ import annotations

from pathlib import Path

import mining_ownership_check


def _rules(source: str, path: str = "src/saitenka/app/foreign.py") -> set[str]:
    return {finding.rule for finding in mining_ownership_check.inspect_source(source, Path(path))}


def test_current_production_tree_has_one_mining_writer() -> None:
    assert mining_ownership_check.inspect_tree() == []


def test_shadow_target_and_index_fields_are_rejected() -> None:
    rules = _rules(
        """
class Foreign:
    def overwrite(self, session):
        self.anki = object()
        self.mine_cfg = object()
        session.mined.add("猫")
"""
    )

    assert rules == {"legacy-owner-field", "legacy-mined-state"}


def test_mining_state_construction_outside_the_owner_is_rejected() -> None:
    rules = _rules(
        """
def build():
    values = MinedSet()
    state = MiningIndexState(None, 0, SeedStatus.EMPTY, values)
    return MiningTransaction()
"""
    )

    assert rules == {"owned-constructor"}


def test_owner_thread_install_acts_are_not_public_mutators() -> None:
    rules = _rules(
        """
def replace(controller, spec, target):
    controller.select_spec(spec)
    controller.publish_prepared_target(target)
    controller.record_expression("猫")
"""
    )

    assert rules == {"owner-mutator"}


def test_declared_composition_and_owner_sites_are_admitted() -> None:
    assert not mining_ownership_check.inspect_source(
        """
def assemble(controller, identity, anki, config, spec):
    target = MiningTarget(identity, anki, config)
    controller.select_spec(spec)
    controller.publish_prepared_target(target)
""",
        Path("src/saitenka/app/session_controller.py"),
    )
    assert not mining_ownership_check.inspect_source(
        """
def own(spec):
    values = MinedSet()
    state = MiningIndexState(None, 0, SeedStatus.EMPTY, values)
    desired = MiningSpec(identity, config)
    target = MiningTarget(identity, anki, config)
    controller.publish_prepared_target(target)
    return MiningTransaction()
""",
        Path("src/saitenka/app/mining_controller.py"),
    )
