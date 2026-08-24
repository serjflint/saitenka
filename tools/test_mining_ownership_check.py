"""Planted controls for the mining ownership census."""

from __future__ import annotations

from pathlib import Path

import mining_ownership_check


def _rules(source: str, path: str = "src/saitenka/app/foreign.py") -> set[str]:
    return {finding.rule for finding in mining_ownership_check.inspect_source(source, Path(path))}


def test_current_production_tree_has_one_mining_writer() -> None:
    assert mining_ownership_check.inspect_tree() == []


def test_legacy_host_fields_are_scoped_to_the_session_controller() -> None:
    source = """
class Foreign:
    def overwrite(self):
        self.anki = object()
"""
    assert _rules(source) == set()
    assert _rules(source, "src/saitenka/app/session_controller.py") == {"legacy-owner-field"}


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


def test_owner_state_writes_and_nested_mutations_are_rejected() -> None:
    rules = _rules(
        """
def replace(controller):
    controller._mining_target = object()
    del controller._mined_store
    controller._mined_index.values.update({"猫"})
    controller._mined_seed.restart()
"""
    )

    assert rules == {"owned-index-mutation", "owned-seed-mutation", "owned-state-write"}


def test_owner_thread_install_acts_are_not_public_mutators() -> None:
    rules = _rules(
        """
def replace(controller, spec, target, identity):
    controller.select_mining_spec(spec)
    controller.publish_mining_target(target)
    controller.clear_mining_target(identity)
    controller.record_mined_expression("猫")
"""
    )

    assert rules == {"owner-mutator"}


def test_declared_composition_and_owner_sites_are_admitted() -> None:
    assert not mining_ownership_check.inspect_source(
        """
def assemble(controller, identity, spec, target):
    controller.select_mining_spec(spec)
    controller.publish_mining_target(target)
    controller.clear_mining_target(identity)
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
    controller.publish_mining_target(target)
    controller.record_mined_expression("猫")
    return MiningTransaction()
""",
        Path("src/saitenka/app/mining_controller.py"),
    )


def test_retired_session_facade_is_rejected() -> None:
    assert _rules(
        """
class SessionController:
    def mine_current(self):
        pass
""",
        "src/saitenka/app/session_controller.py",
    ) == {"retired-facade"}


def test_nested_app_modules_are_scanned(tmp_path) -> None:
    nested = tmp_path / "commands"
    nested.mkdir()
    path = nested / "foreign.py"
    path.write_text("target = MiningTarget(identity, anki, config)\n", encoding="utf-8")

    findings = mining_ownership_check.inspect_tree(tmp_path)

    assert [(finding.path, finding.rule) for finding in findings] == [(path, "owned-constructor")]
