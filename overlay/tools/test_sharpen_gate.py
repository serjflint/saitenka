"""Tests run by `poe loop-tools-test`, or explicitly:
uv run python -m pytest tools/test_sharpen_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sharpen_gate as sg

CUT = "overlay.app.mod"


def _kinds(before: str, after: str, cut: str = "") -> set[str]:
    return {v.kind for v in sg.anticheat_diff(before, after, cut)}


# --- Arm B: additive / strengthening are clean --------------------------------------------------


def test_purely_additive_edit_is_clean():
    before = "def test_x():\n    assert f() == 1\n"
    after = "def test_x():\n    assert f() == 1\n    assert g() == 2\n"
    assert _kinds(before, after) == set()


def test_strengthening_a_weak_assert_is_allowed():
    before = "def test_x():\n    assert f() is not None\n"
    after = "def test_x():\n    assert f() == 5\n"  # weak → strong: a real sharpen
    assert _kinds(before, after) == set()


# --- Arm B: weakening is bounced (the eval's cross-tier bypass) ----------------------------------


def test_equality_downgraded_to_truthiness_is_weakened():
    before = "def test_x():\n    assert result == 5\n"
    after = "def test_x():\n    assert result is not None\n"  # the classic silent lobotomy
    assert "weakened" in _kinds(before, after)


def test_dropping_a_strong_assert_even_if_count_holds_is_weakened():
    before = "def test_x():\n    assert a == 1\n    assert b == 2\n"
    after = "def test_x():\n    assert a == 1\n    assert b > 0\n"  # b's equality → partial
    assert "weakened" in _kinds(before, after)


def test_fewer_assertions_is_weakened():
    before = "def test_x():\n    assert a == 1\n    assert b == 2\n"
    after = "def test_x():\n    assert a == 1\n"
    assert "weakened" in _kinds(before, after)


# --- Arm B: trivial + cut-derived ---------------------------------------------------------------


def test_assert_true_is_trivial():
    before = "def test_x():\n    assert a == 1\n"
    after = "def test_x():\n    assert True\n"
    assert "trivial" in _kinds(before, after)


def test_self_comparison_is_trivial():
    after = "def test_x():\n    assert value == value\n"
    assert "trivial" in _kinds("def test_x():\n    pass\n", after)


def test_bare_name_cut_constant_is_flagged():
    after = (
        "from overlay.app.mod import THRESHOLD\n\n"
        "def test_x():\n    assert score() == THRESHOLD\n"  # expected value read from the CUT
    )
    assert "cut-derived" in _kinds("def test_x():\n    pass\n", after, CUT)


def test_module_qualified_cut_constant_is_flagged():
    after = "def test_x():\n    assert oid() == mod.TIP_ID\n"
    assert "cut-derived" in _kinds("def test_x():\n    pass\n", after, CUT)


def test_local_constant_is_not_cut_derived():
    after = "EXPECTED = 5\n\ndef test_x():\n    assert score() == EXPECTED\n"  # local, not from CUT
    assert "cut-derived" not in _kinds("def test_x():\n    pass\n", after, CUT)


# --- Arm B: redundancy-merge is allowed, real delete is not -------------------------------------


def test_merging_a_test_is_not_a_removal_when_asserts_reappear():
    before = "def test_a():\n    assert f(1) == 1\n\ndef test_b():\n    assert f(2) == 2\n"
    after = "def test_ab():\n    assert f(1) == 1\n    assert f(2) == 2\n"  # merged, asserts kept
    assert _kinds(before, after) == set()


def test_deleting_a_test_and_losing_its_asserts_is_removed():
    before = "def test_a():\n    assert f(1) == 1\n\ndef test_b():\n    assert f(2) == 2\n"
    after = "def test_a():\n    assert f(1) == 1\n"  # test_b's assertion is gone
    assert "removed" in _kinds(before, after)


# --- Arm A: efficacy_gate logic via an injected fake replay --------------------------------------


def _fake_replay(killed: set[tuple[str, int]]) -> sg.Replay:
    def replay(_module, m, _cmd, _cwd):
        return (m.operator, m.occurrence) in killed

    return replay


def _m(op: str, occ: int) -> sg.Mutant:
    return sg.Mutant(op, occ, occ)


def test_efficacy_gate_passes_when_targets_earned_and_control_holds():
    targets = [_m("A", 1), _m("B", 2)]
    control = [_m("K", 1), _m("K", 2)]
    killed = {("A", 1), ("K", 1), ("K", 2)}  # A earned; B equivalent; all control still killed
    rep = sg.efficacy_gate(
        Path("m.py"), targets, control, [], cwd=Path(), replay=_fake_replay(killed)
    )
    assert [m.operator for m in rep.earned] == ["A"]
    assert rep.regressed == []
    assert rep.ok


def test_efficacy_gate_bounces_when_a_control_mutant_regresses():
    targets = [_m("A", 1)]
    control = [_m("K", 1), _m("K", 2)]
    killed = {("A", 1), ("K", 1)}  # K@2 previously killed, now survives → lobotomy
    rep = sg.efficacy_gate(
        Path("m.py"), targets, control, [], cwd=Path(), replay=_fake_replay(killed)
    )
    assert rep.score_dropped
    assert not rep.ok


def test_full_control_catches_the_narrowing_bypass_arm_b_misses():
    # The eval's within-tier bypass: `assert full == exp` → `assert full['k'] == exp['k']` + a new
    # strong assert that earns a real kill. Arm B sees equal strong-count → clean:
    before = "def test_x():\n    assert full == exp\n"
    after = "def test_x():\n    assert full['k'] == exp['k']\n    assert other() == 9\n"
    assert _kinds(before, after) == set()  # Arm B blind, as documented
    # …but Arm A's FULL control includes the mutant the whole-dict assert used to kill; the narrowed
    # assert lets it survive → regressed → BOUNCE.
    targets = [_m("NEW", 1)]  # the added assert earns a kill
    control = [_m("WHOLE", 1)]  # the mutant the dropped whole-dict equality used to kill
    killed = {("NEW", 1)}  # WHOLE now survives
    rep = sg.efficacy_gate(
        Path("m.py"), targets, control, [], cwd=Path(), replay=_fake_replay(killed)
    )
    assert rep.earned and rep.score_dropped and not rep.ok


def _adhoc_files(tmp_path: Path) -> tuple[Path, Path]:
    module = tmp_path / "module.py"
    test_file = tmp_path / "test_module.py"
    module.write_text("def enabled():\n    return True\n", encoding="utf-8")
    test_file.write_text(
        "def test_enabled():\n    assert enabled() is not None\n", encoding="utf-8"
    )
    return module, test_file


def test_preservation_adhoc_requires_old_and_new_tests_to_kill_the_witness(tmp_path):
    module, test_file = _adhoc_files(tmp_path)
    before = "def test_enabled():\n    assert enabled() is True\n"

    rep = sg.preservation_adhoc_gate(
        module.relative_to(tmp_path),
        "return True",
        "return False",
        test_file.relative_to(tmp_path),
        before,
        ["TEST"],
        cwd=tmp_path,
        run=lambda _cmd: 1,
    )

    assert rep.killed_before and rep.killed_after and rep.ok
    assert module.read_text(encoding="utf-8") == "def enabled():\n    return True\n"
    assert test_file.read_text(encoding="utf-8").endswith("is not None\n")


def test_preservation_adhoc_bounces_a_lost_preexisting_kill(tmp_path):
    module, test_file = _adhoc_files(tmp_path)
    before = "def test_enabled():\n    assert enabled() is True\n"

    def run(_cmd):
        return 1 if "is True" in test_file.read_text(encoding="utf-8") else 0

    rep = sg.preservation_adhoc_gate(
        module.relative_to(tmp_path),
        "return True",
        "return False",
        test_file.relative_to(tmp_path),
        before,
        ["TEST"],
        cwd=tmp_path,
        run=run,
    )

    assert rep.killed_before and not rep.killed_after
    assert not rep.ok
    assert module.read_text(encoding="utf-8") == "def enabled():\n    return True\n"
    assert test_file.read_text(encoding="utf-8").endswith("is not None\n")
