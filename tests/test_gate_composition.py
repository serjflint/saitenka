"""CI hand-copies the gate's composition; these bind the copies that have a `[tool.poe.tasks]` SSOT.

The `tests` / `tests-ft` legs invoke pytest directly on purpose — poe is absent from the lean `test`
group, which is the point of those legs — so the marker expression cannot be deduplicated, only bound.
It had already drifted: both legs dropped `live`, inert only because all four live modules carry a
module-level `SAITENKA_LIVE` skipif.

The pair list below is a third hand-written copy of the same fact. That is the trade, not an oversight:
two copies that drift *silently* become three that fail *loudly*.

Census, so the next reader knows what is NOT bound: `ci.yml` has four marker-carrying pytest
invocations — the two bound here, plus two in the `libasslite` job (`not integration`, `integration`)
that map to no poe task. `e2e.yml` has a fifth, also with no SSOT and not drifted. Requiring every
invocation to be registered was considered and dropped: it needs a second entry kind for the unbindable
ones and roughly doubles this file, to guard a leg nobody has written yet.

Not visible to an expression comparison: both bound legs also carry
`--ignore=tests/test_stress_memory.py`, a CI-only divergence with its own documented reason (the lean
group omits `pytest-memray`, so strict markers would error at collection).
"""

from __future__ import annotations

import shlex
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"

#: (job, step name) → the poe task whose marker expression that step must carry verbatim. Keyed by step
#: rather than by expression: expressions collide (`not integration` is also `loop-tools-test`'s) and
#: change under the very drift this file exists to catch.
BOUND_STEPS = {
    ("tests", "Tests"): "test",
    ("tests-ft", "Tests (free-threaded)"): "test-ft",
}


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _poe_tasks() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["poe"][
        "tasks"
    ]


def _marker_expression(command: str) -> str | None:
    """The argument to `-m`, or None when the command selects no markers."""
    tokens = shlex.split(command)
    if "-m" not in tokens:
        return None
    return tokens[tokens.index("-m") + 1]


def _task_command(task: object) -> str:
    """poe tasks are a bare string, or a table keyed `shell` / `cmd`."""
    if isinstance(task, str):
        return task
    assert isinstance(task, dict)
    return task.get("shell") or task["cmd"]


def _step_command(job: str, name: str) -> str:
    steps = _workflow()["jobs"][job]["steps"]
    return next(step["run"] for step in steps if step.get("name") == name)


@pytest.mark.parametrize(("step", "task_name"), sorted(BOUND_STEPS.items()))
def test_ci_leg_carries_its_poe_task_marker_expression(
    step: tuple[str, str], task_name: str
) -> None:
    """Compare the expression, not its marker names — `not (slow or live)` and `slow or live` have
    identical name sets, so a name-set binding would pass an inversion."""
    job, name = step
    assert _marker_expression(_step_command(job, name)) == _marker_expression(
        _task_command(_poe_tasks()[task_name])
    )


def test_gate_matrix_runs_every_leg_of_poe_all() -> None:
    matrix = _workflow()["jobs"]["gate"]["strategy"]["matrix"]["task"]
    assert set(matrix) == set(_poe_tasks()["all"])


def test_free_threaded_split_installs_the_gate_runtime_dependencies() -> None:
    step = "Install MeCab (fugashi build dep) and libass (libasslite runtime)"
    assert _step_command("tests-ft", step) == _step_command("gate", step)


def test_the_bound_expressions_are_not_vacuous() -> None:
    """Negative control: a mistyped YAML path yields None on both sides, and `None == None` would make
    every assertion above pass while binding nothing."""
    assert BOUND_STEPS
    for job, name in BOUND_STEPS:
        assert _marker_expression(_step_command(job, name))
    assert _workflow()["jobs"]["gate"]["strategy"]["matrix"]["task"]
