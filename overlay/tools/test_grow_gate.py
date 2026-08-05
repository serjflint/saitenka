"""Tests for the Grow teeth-gate. Run explicitly (tools/ is outside `poe all`):
    uv run python -m pytest tools/test_grow_gate.py

Every arm is exercised through its injected primitive (replay / RunTest / CoverageFn / RunExit), so no
real cosmic-ray, pytest, or coverage subprocess runs here — the same pattern `test_sharpen_gate.py` uses.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import grow_gate as gg
import sharpen_gate as sg

# --- Arm 1: growth_gate (property-mutant survives-old + killed-new) ------------------------------


def _replay(killers: set[str]) -> sg.Replay:
    """A fake replay: the mutant is 'killed' by any command whose first token is in ``killers``."""

    def replay(_module, _m, cmd, _cwd):
        return cmd[0] in killers

    return replay


_M = sg.Mutant("op", 1, 1)


def test_growth_passes_when_survives_old_and_killed_new():
    rep = gg.growth_gate(Path("m.py"), _M, ["OLD"], ["NEW"], cwd=Path(), replay=_replay({"NEW"}))
    assert rep.survived_old and rep.killed_new and rep.ok


def test_growth_bounces_a_redundant_mutant_the_old_suite_already_kills():
    # old suite kills it too → not previously-uncaught → redundant restatement, not growth
    rep = gg.growth_gate(
        Path("m.py"), _M, ["OLD"], ["NEW"], cwd=Path(), replay=_replay({"OLD", "NEW"})
    )
    assert not rep.survived_old
    assert not rep.ok


def test_growth_bounces_a_vacuous_test_that_does_not_kill_the_mutant():
    rep = gg.growth_gate(Path("m.py"), _M, ["OLD"], ["NEW"], cwd=Path(), replay=_replay(set()))
    assert rep.survived_old and not rep.killed_new
    assert not rep.ok


# --- Arm 2: liveness_gate + its AST helpers -----------------------------------------------------

_CUT = "def compute(x):\n    return x * 2 + 1\n"


def _runner(cut: str = _CUT):
    """A RunTest that exec()s the CUT + a candidate test source and reports pass/fail — a hermetic stand-in
    for the real `run_pytest_source` subprocess."""

    def run(src: str) -> bool:
        ns: dict = {}
        try:
            exec(compile(cut + "\n" + src, "<t>", "exec"), ns)  # noqa: S102 — toy CUT, test-only
            ns["test_it"]()
            return True
        except Exception:  # noqa: BLE001 — any raise = the test failed
            return False

    return run


def test_liveness_passes_a_real_equality_oracle():
    src = "def test_it():\n    assert compute(4) == 9\n"
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert rep.live == [0]
    assert rep.ok


def test_liveness_bounces_assert_true():
    src = "def test_it():\n    compute(4)\n    assert True\n"
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert rep.trivial == [0]
    assert not rep.ok


def test_liveness_bounces_a_swallowed_assert_static_count_would_miss():
    # the assert exists (static count = 1) but try/except eats it → nothing is actually asserted
    src = (
        "def test_it():\n"
        "    try:\n"
        "        assert compute(4) == 999\n"
        "    except AssertionError:\n"
        "        pass\n"
    )
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert rep.dead == [0]
    assert not rep.ok


def test_liveness_bounces_a_test_with_no_assertions():
    src = "def test_it():\n    compute(4)\n"
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert rep.no_asserts
    assert not rep.ok


def test_liveness_bounces_a_test_red_on_pristine_code():
    src = "def test_it():\n    assert compute(4) == 8\n"  # wrong expectation → red on correct code
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert not rep.passes_pristine
    assert not rep.ok


def test_liveness_bounces_when_a_live_assert_is_mixed_with_a_trivial_one():
    src = "def test_it():\n    assert compute(4) == 9\n    assert True\n"
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert rep.live == [0]
    assert rep.trivial == [1]
    assert not rep.ok  # a stray assert-True is still sloppy — bounce, like sharpen


def test_negate_assert_only_touches_the_target_function():
    src = "def test_a():\n    assert one() == 1\n\ndef test_b():\n    assert two() == 2\n"
    out = gg._negate_assert_in(src, "test_b", 0)
    assert "assert one() == 1" in out  # test_a untouched
    assert "assert not two() == 2" in out or "assert not (two() == 2)" in out


# --- Arm 3: context_delta_gate ------------------------------------------------------------------


def test_context_delta_passes_when_the_grown_suite_reaches_new_lines():
    cov = {("OLD",): {1, 2}, ("NEW",): {1, 2, 14, 20}}
    rep = gg.context_delta_gate(["OLD"], ["NEW"], lambda cmd: cov[tuple(cmd)])
    assert rep.delta == {14, 20}
    assert rep.ok


def test_context_delta_bounces_when_no_new_line_is_reached():
    cov = {("OLD",): {1, 2, 3}, ("NEW",): {1, 2}}  # grown test only re-covers old lines
    rep = gg.context_delta_gate(["OLD"], ["NEW"], lambda cmd: cov[tuple(cmd)])
    assert rep.delta == set()
    assert not rep.ok


# --- Arm 4: concurrency_gate --------------------------------------------------------------------


def test_concurrency_passes_when_both_pass_and_control_oracle_is_live():
    codes = {("reg",): 0, ("ctl",): 0}  # both PASS — the shipped test_cache_race.py structure
    rep = gg.concurrency_gate(
        ["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)], control_has_live_oracle=True
    )
    assert rep.ok


def test_concurrency_bounces_when_the_control_oracle_is_not_live():
    # a passing control with a vacuous oracle proves nothing about the forced schedule (C6)
    codes = {("reg",): 0, ("ctl",): 0}
    rep = gg.concurrency_gate(
        ["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)], control_has_live_oracle=False
    )
    assert not rep.ok


def test_concurrency_bounces_when_the_control_does_not_pass():
    # control failing = the bug did NOT reproduce unguarded → the teeth were never demonstrated
    codes = {("reg",): 0, ("ctl",): 1}
    rep = gg.concurrency_gate(
        ["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)], control_has_live_oracle=True
    )
    assert not rep.control_passed
    assert not rep.ok


def test_concurrency_bounces_when_the_regression_does_not_pass():
    codes = {("reg",): 1, ("ctl",): 0}
    rep = gg.concurrency_gate(
        ["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)], control_has_live_oracle=True
    )
    assert not rep.regression_passed
    assert not rep.ok


# --- Arm 2 extension: pytest.raises / warns are live oracles (C8) --------------------------------


def test_liveness_accepts_a_pytest_raises_only_test():
    # no assert node, but a pytest.raises block IS a falsifiable oracle → not no_asserts, has teeth
    src = "def test_it():\n    with pytest.raises(ValueError):\n        compute('bad')\n"
    rep = gg.liveness_gate(src, "test_it", lambda _s: True)  # passes on pristine
    assert not rep.no_asserts
    assert rep.raises == 1
    assert rep.ok


def test_liveness_still_bounces_when_neither_assert_nor_raises_present():
    src = "def test_it():\n    compute(4)\n"
    rep = gg.liveness_gate(src, "test_it", _runner())
    assert rep.no_asserts
    assert not rep.ok


# --- The Grow↔Sharpen boundary: real adds-only diff (C4) -----------------------------------------


def test_additive_passes_a_pure_addition():
    before = "def test_x():\n    assert f() == 1\n"
    after = "def test_x():\n    assert f() == 1\n    assert g() == 2\n"
    assert gg.additive_gate(before, after).ok


def test_additive_bounces_a_same_tier_value_change_sharpen_gate_misses():
    # The exact case anticheat_diff waves through as 'additive' (C4) — a change-detector / mutative edit.
    before = "def test_x():\n    assert route(cfg) == 1\n"
    after = "def test_x():\n    assert route(cfg) == 2\n"
    assert sg.anticheat_diff(before, after) == []  # sharpen_gate is blind here (documents the gap)
    rep = gg.additive_gate(before, after)
    assert rep.removed
    assert not rep.ok


def test_additive_bounces_a_removed_assert():
    before = "def test_x():\n    assert a == 1\n    assert b == 2\n"
    after = "def test_x():\n    assert a == 1\n"
    assert not gg.additive_gate(before, after).ok


def test_additive_allows_moving_an_assert_to_another_test():
    before = "def test_a():\n    assert f(1) == 1\n\ndef test_b():\n    assert f(2) == 2\n"
    after = "def test_ab():\n    assert f(1) == 1\n    assert f(2) == 2\n"  # merged, same nodes
    assert gg.additive_gate(before, after).ok


# --- Arm 1 off-allowlist: growth_adhoc_gate (C2) -------------------------------------------------


def _adhoc_run(fail_on: set[str]):
    """A fake runner: a cmd 'fails' (non-zero) under the mutant iff its first token is in ``fail_on``."""

    def run(cmd):
        return 1 if cmd[0] in fail_on else 0

    return run


def test_growth_adhoc_passes_when_old_survives_and_new_kills():
    rep = gg.growth_adhoc_gate(
        Path("cut.py"),
        "a",
        "b",
        ["OLD"],
        ["NEW"],
        _adhoc_run({"NEW"}),
        apply_mutation=lambda *_: True,
        restore=lambda *_: None,
        cwd=Path(),
    )
    assert rep.applied and rep.survived_old and rep.killed_new
    assert rep.ok


def test_growth_adhoc_bounces_when_the_old_suite_already_catches_the_mutant():
    rep = gg.growth_adhoc_gate(
        Path("cut.py"),
        "a",
        "b",
        ["OLD"],
        ["NEW"],
        _adhoc_run({"OLD", "NEW"}),
        apply_mutation=lambda *_: True,
        restore=lambda *_: None,
        cwd=Path(),
    )
    assert not rep.survived_old
    assert not rep.ok


def test_growth_adhoc_bounces_when_the_grown_test_does_not_kill():
    rep = gg.growth_adhoc_gate(
        Path("cut.py"),
        "a",
        "b",
        ["OLD"],
        ["NEW"],
        _adhoc_run(set()),
        apply_mutation=lambda *_: True,
        restore=lambda *_: None,
        cwd=Path(),
    )
    assert not rep.killed_new
    assert not rep.ok


def test_growth_adhoc_bounces_and_does_not_restore_when_the_mutation_wont_apply():
    restored = {"v": False}

    def restore(*_):
        restored["v"] = True

    rep = gg.growth_adhoc_gate(
        Path("cut.py"),
        "a",
        "b",
        ["OLD"],
        ["NEW"],
        _adhoc_run(set()),
        apply_mutation=lambda *_: False,
        restore=restore,
        cwd=Path(),
    )
    assert not rep.applied
    assert not rep.ok
    assert not restored["v"]  # nothing was written → nothing to restore
