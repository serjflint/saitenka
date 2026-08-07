"""Planted +/- controls for the doc↔code checker (tools/docs_check.py, `poe docs-refs`/`docs-consts`).

A drift gate is only worth its green: each detector must FAIL on a planted stale ref / constant and
PASS on the real tree. Positive controls assert the live docs are clean (so `poe all` stays green);
negative controls plant one drift each and assert exactly it is caught (so the gate can't rot into a
no-op). HANDBOOK.md's two-sided shape — required claim resolves AND the forbidden one is caught.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DC = Path(__file__).resolve().parent.parent / "tools" / "docs_check.py"


def _mod():
    spec = importlib.util.spec_from_file_location("_docs_check", _DC)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m  # @dataclass resolves types via sys.modules[__module__] (3.14)
    spec.loader.exec_module(m)
    return m


# --- positive controls: the real tree is clean (these are the gate itself) -----------------------


def test_refs_clean_on_real_tree() -> None:
    assert _mod().check_refs() == []


def test_consts_clean_on_real_tree() -> None:
    assert _mod().check_consts() == []


# --- refs: negative controls (one planted drift each) --------------------------------------------


def test_refs_catches_unknown_poe_task() -> None:
    dc = _mod()
    fails = dc._ref_failures("run `poe zz-planted-missing` now", "FAKE.md", dc._poe_tasks())
    assert any("zz-planted-missing" in f for f in fails)


def test_refs_pass_a_real_poe_task_in_code_font() -> None:
    dc = _mod()
    assert dc._ref_failures("the `poe all` gate", "FAKE.md", dc._poe_tasks()) == []


def test_refs_ignore_prose_poe_not_in_code_font() -> None:
    # "a poe gate" is English, not a task reference — only code-font invocations are refs.
    dc = _mod()
    assert dc._ref_failures("this is a poe gate, a poe task", "FAKE.md", dc._poe_tasks()) == []


def test_refs_catches_missing_agents_path() -> None:
    dc = _mod()
    fails = dc._ref_failures("see `.agents/skills/__planted_missing__/SKILL.md`", "FAKE.md", set())
    assert any("__planted_missing__" in f for f in fails)


def test_refs_pass_a_real_agents_path() -> None:
    dc = _mod()
    assert dc._ref_failures("see `.agents/rules/searching.md`", "FAKE.md", set()) == []


def test_refs_catches_missing_module_file() -> None:
    dc = _mod()
    fails = dc._ref_failures("`render/__planted_missing__.py` is the core", "FAKE.md", set())
    assert any("__planted_missing__" in f for f in fails)


def test_refs_pass_a_real_module_file() -> None:
    dc = _mod()
    assert dc._ref_failures("`render/banded.py` is the core", "FAKE.md", set()) == []


# --- consts: negative controls (one planted drift each) ------------------------------------------


def _spec(dc, ident: str, where: str, value):
    return dc.ConstSpec(ident=ident, where=where, resolve=lambda: value)


def test_consts_catches_wrong_value() -> None:
    dc = _mod()
    reg = {"x": _spec(dc, "x", "Foo", 256)}
    fails = dc._consts_failures({"x": ("999", "`Foo`")}, reg)
    assert any("= 999 in the doc but 256 in code" in f for f in fails)


def test_consts_catches_wrong_attribution() -> None:
    dc = _mod()
    reg = {"x": _spec(dc, "x", "TooltipOptions", 256)}
    fails = dc._consts_failures({"x": ("256", "`PerfOptions`")}, reg)
    assert any("attributed to" in f and "TooltipOptions" in f for f in fails)


def test_consts_catches_unregistered_doc_claim() -> None:
    dc = _mod()
    fails = dc._consts_failures({"orphan": ("7", "`Foo`")}, {})
    assert any("orphan" in f and "not registered" in f for f in fails)


def test_consts_catches_registered_but_missing_from_doc() -> None:
    dc = _mod()
    reg = {"gone": _spec(dc, "gone", "Foo", 1)}
    fails = dc._consts_failures({}, reg)
    assert any("gone" in f and "not found in the ARCHITECTURE.md table" in f for f in fails)


def test_consts_pass_on_matching_value_and_attribution() -> None:
    dc = _mod()
    reg = {"x": _spec(dc, "x", "Foo", 0.4)}
    assert dc._consts_failures({"x": ("0.4", "`Foo`, `Bar`")}, reg) == []
