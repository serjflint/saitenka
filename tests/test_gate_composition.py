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
E2E = ROOT / ".github" / "workflows" / "e2e.yml"

#: (job, step name) → the poe task whose marker expression that step must carry verbatim. Keyed by step
#: rather than by expression: expressions collide (`not integration` is also `loop-tools-test`'s) and
#: change under the very drift this file exists to catch.
BOUND_STEPS = {
    ("tests", "Tests"): "test",
    ("tests-ft", "Tests (free-threaded)"): "test-ft",
}

#: Runners `libasslite-bundle` publishes a wheel for — its release matrix is arm64 macOS, manylinux
#: x86_64/aarch64 and win_amd64. macOS x86_64 is absent, which is why e2e no longer runs there.
_BUNDLE_WHEEL_RUNNERS = {"ubuntu-latest", "windows-latest", "macos-latest"}


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _e2e_workflow() -> dict:
    return yaml.safe_load(E2E.read_text(encoding="utf-8"))


def _poe_tasks() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["poe"][
        "tasks"
    ]


def _optional_dependencies() -> dict[str, list[str]]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
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


def test_free_threaded_split_uses_the_published_bundle_runtime() -> None:
    install = _step_command("tests-ft", "Install MeCab (fugashi build dep) and CJK fonts")
    assert shlex.split(install) == [
        "sudo",
        "apt-get",
        "update",
        "&&",
        "sudo",
        "apt-get",
        "install",
        "-y",
        "libmecab-dev",
        "fonts-noto-cjk",
    ]

    extras = _optional_dependencies()
    assert "saitenka[subtitle-geometry]" in extras["full"]
    assert any(
        requirement.startswith("libasslite==") for requirement in extras["subtitle-geometry"]
    )
    bundle_requirement = next(
        requirement
        for requirement in extras["subtitle-geometry-bundle"]
        if requirement.startswith("libasslite-bundle==")
    )

    sync_command = _step_command("tests-ft", "Sync (test group only)").replace("\\\n", " ")
    sync = [
        shlex.split(line, comments=True)
        for line in sync_command.splitlines()
        if shlex.split(line, comments=True)
    ]
    assert sync == [
        ["uv", "python", "install"],
        ["uv", "sync", "--locked", "--extra", "full", "--no-default-groups", "--group", "test"],
        [
            "uv",
            "pip",
            "install",
            "--no-deps",
            "--only-binary",
            "libasslite-bundle",
            "--no-sources-package",
            "libasslite-bundle",
            bundle_requirement,
        ],
    ]

    # Found by position of `uv`, not by prefix: the step may be wrapped (the resource probe is), and
    # the claim is that pytest runs against the env just synced above — never that nothing precedes it.
    tests = shlex.split(_step_command("tests-ft", "Tests (free-threaded)"))
    assert tests[tests.index("uv") : tests.index("uv") + 3] == ["uv", "run", "--no-sync"]


def _jobs_selecting_tests_broadly() -> dict[str, dict]:
    """Jobs that run whatever a marker expression or `smoke-live` selects, rather than an enumerated
    list of test files. Only these can reach an ASS-oracle test, so only these need the libass
    runtime; `windows-regressions` names its files and `*-measure` run a script, not pytest."""

    def broad(step: dict) -> bool:
        run = str(step.get("run", ""))
        return "smoke-live" in run or ("pytest" in run and " -m " in run)

    return {
        name: job
        for name, job in _e2e_workflow()["jobs"].items()
        if any(broad(step) for step in job["steps"])
    }


def test_e2e_installs_the_same_bundle_runtime_the_extra_pins() -> None:
    """The `full` sync installs libasslite, whose ASS-oracle tests error rather than skip when no
    libass can be dlopened. Every leg that syncs it must therefore reach one, and the version must
    track the extra's pin — a bumped pin with a stale workflow tests a runtime nobody ships."""
    bundle_requirement = next(
        requirement
        for requirement in _optional_dependencies()["subtitle-geometry-bundle"]
        if requirement.startswith("libasslite-bundle==")
    )
    expected = [
        "uv",
        "pip",
        "install",
        "--no-deps",
        "--only-binary",
        "libasslite-bundle",
        "--no-sources-package",
        "libasslite-bundle",
        bundle_requirement,
    ]

    # Censused rather than named: the GUI tier moved to its own job once it ran per mpv version, and
    # a third broadly-selecting job would otherwise reach the ERROR this install exists to prevent.
    assert len(_jobs_selecting_tests_broadly()) == 2
    for name, job in _jobs_selecting_tests_broadly().items():
        steps = {step["name"]: step for step in job["steps"] if "name" in step}
        install = steps["Install the published libass runtime"]
        assert shlex.split(install["run"]) == expected, name
        # Unconditional, which only holds while every leg is a platform the bundle publishes a wheel
        # for. Adding one it doesn't (macOS x86_64 is the standing example) silently returns that leg
        # to the same ERROR.
        assert "if" not in install, name
        # The bundle is pip-installed on top of the locked env. `uv run` syncs inexactly today, so
        # this pins the behaviour rather than depending on it; only an exact `uv sync` prunes.
        for step in job["steps"]:
            run = str(step.get("run", ""))
            if run.startswith(("uv run", "xvfb-run")):
                assert "--no-sync" in shlex.split(run), f"{name}: {step.get('name')}"

    assert set(_e2e_workflow()["jobs"]["e2e"]["strategy"]["matrix"]["os"]) <= _BUNDLE_WHEEL_RUNNERS


def _gui_legs() -> list[dict]:
    return _e2e_workflow()["jobs"]["e2e-gui"]["strategy"]["matrix"]["include"]


def test_the_gui_tier_runs_against_every_mpv_floor_the_package_declares() -> None:
    """A floor nothing runs at is a claim nothing checks — a regression breaking the declared minimum
    would ship green against a later mpv, which is the shape this whole tier exists to catch. Bound to
    the constants, so raising a floor without adding a leg fails here rather than silently."""
    from saitenka.app.doctor import MPV_MIN
    from saitenka.mpvio.launch import NATIVE_GEOMETRY_MPV_MIN

    covered = [leg["expect"] for leg in _gui_legs()]
    for floor in (MPV_MIN, NATIVE_GEOMETRY_MPV_MIN):
        assert ".".join(str(part) for part in floor) in covered

    # Counted over the list, not a set: a set makes this structurally 0-or-1 and the assertion
    # degenerates into "at least one floats", which a second floating leg would satisfy.
    assert covered.count("") == 1


def test_every_downloaded_mpv_is_pinned_by_hash_or_resolved_through_the_release_api() -> None:
    """A URL fetched over the wire and executed is a supply-chain input. A pinned one carries its
    SHA256; the floating one names no URL to pin, so it must resolve through `gh` rather than a
    hand-built link — which also spares us percent-encoding the `@` in those tags."""
    for leg in _gui_legs():
        source = leg["appimage"]
        if not source:
            continue  # apt
        if source == "latest":
            assert "sha256" not in leg
            continue
        assert source.startswith("https://github.com/")
        assert len(leg["sha256"]) == 64


def test_every_gui_leg_runs_the_identical_suite() -> None:
    """The split is per test (the `mpv_min` marker), not per leg. If a leg ran its own selection the
    floors would drift apart silently, which is what a marker expression per leg would have cost."""
    steps = _e2e_workflow()["jobs"]["e2e-gui"]["steps"]
    gui = [step for step in steps if str(step.get("name", "")).startswith("GUI tier")]
    assert len(gui) == 1
    # Compared verbatim, and required unconditional. Asserting only that the `run` mentions
    # `smoke-live` admits both drift shapes this exists to stop: a `${{ matrix… }}` selection
    # appended to the command, and an `if:` that quietly drops a leg from the tier entirely.
    assert "if" not in gui[0], "a conditional GUI step silently drops a leg"
    assert shlex.split(gui[0]["run"]) == [
        "xvfb-run",
        "-a",
        "uv",
        "run",
        "--no-sync",
        "poe",
        "smoke-live",
    ]


def _triggers(workflow: dict) -> dict:
    """A workflow's `on:` block. YAML 1.1 reads a bare `on` as the boolean True, so PyYAML keys the
    block under `True` rather than the string — indexing `["on"]` raises KeyError."""
    return workflow[True]


def _auto_pushing_steps() -> list[tuple[str, dict]]:
    """Every e2e step that publishes to the gh-pages dashboard, as (job, step)."""
    return [
        (job_name, step)
        for job_name, job in _e2e_workflow()["jobs"].items()
        for step in job["steps"]
        if step.get("with", {}).get("auto-push") is True
    ]


def test_every_dashboard_publish_is_gated_on_the_store_input() -> None:
    """A dispatch measures by default and publishes only when asked. Ungated, every scratch run puts a
    point on the live dashboard that no commit on `main` explains, and the removal is a hand-edit of
    `gh-pages`. Censused rather than named, so a third publishing step cannot land unguarded."""
    gate = "github.event_name != 'workflow_dispatch' || inputs.store"
    for job_name, step in _auto_pushing_steps():
        assert step.get("if") == gate, f"{job_name}/{step.get('name')} publishes ungated"

    store_input = _triggers(_e2e_workflow())["workflow_dispatch"]["inputs"]["store"]
    assert store_input["type"] == "boolean"
    assert store_input["default"] is False


def test_the_publish_census_is_not_empty() -> None:
    """Negative control: a renamed key or a restructured `with:` would make the census empty, and an
    empty census makes the gate assertion above pass while binding nothing. A floor, not equality:
    the gate test iterates whatever this finds, so a correctly-gated third step is fine — but
    truthiness would let ONE of the two silently drop out and still pass."""
    assert len(_auto_pushing_steps()) >= 2


def test_the_bound_expressions_are_not_vacuous() -> None:
    """Negative control: a mistyped YAML path yields None on both sides, and `None == None` would make
    every assertion above pass while binding nothing."""
    assert BOUND_STEPS
    for job, name in BOUND_STEPS:
        assert _marker_expression(_step_command(job, name))
    assert _workflow()["jobs"]["gate"]["strategy"]["matrix"]["task"]
