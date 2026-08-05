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


def test_concurrency_passes_when_regression_green_and_control_red():
    codes = {("reg",): 0, ("ctl",): 1}  # regression passes, negative control fails
    rep = gg.concurrency_gate(["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)])
    assert rep.ok


def test_concurrency_bounces_a_control_that_does_not_fail():
    # a negative control that passes proves the forced schedule has no teeth (vacuous race test)
    codes = {("reg",): 0, ("ctl",): 0}
    rep = gg.concurrency_gate(["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)])
    assert not rep.control_failed
    assert not rep.ok


def test_concurrency_bounces_a_regression_that_does_not_pass():
    codes = {("reg",): 1, ("ctl",): 1}
    rep = gg.concurrency_gate(["reg"], ["ctl"], lambda cmd: codes[tuple(cmd)])
    assert not rep.regression_passed
    assert not rep.ok
