"""Rot-guard for the canonical mutation allowlist (`tools/mutate/run.py::TARGETS`).

TARGETS is the single source of truth for the pure-core mutation set (AGENTS.md "Mutation auditing"
points here; the Sharpen harness gates Efficacy on `poe mutate --list`). A moved/renamed module or test
would silently break `poe mutate <m>` and the harness's Efficacy axis — this catches that drift in the
fast gate. It does NOT re-encode the add/remove *policy* (that is human judgement, in the run.py
docstring); it only asserts every listed path still resolves.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_OVERLAY_ROOT = Path(__file__).resolve().parent.parent
_RUN_PY = _OVERLAY_ROOT / "tools" / "mutate" / "run.py"


def _load_targets() -> dict[str, tuple[str, str]]:
    spec = importlib.util.spec_from_file_location("_mutate_run", _RUN_PY)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TARGETS


def test_every_mutation_target_resolves_to_existing_module_and_tests() -> None:
    missing: list[str] = []
    for name, (module, tests) in _load_targets().items():
        if not (_OVERLAY_ROOT / module).is_file():
            missing.append(f"{name}: module {module}")
        missing += [f"{name}: test {t}" for t in tests.split() if not (_OVERLAY_ROOT / t).is_file()]
    assert not missing, "mutation TARGETS drifted from the tree:\n" + "\n".join(missing)
