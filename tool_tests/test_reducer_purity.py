"""Planted controls for the reducer-purity gate — a rule matching nothing enforces nothing.

The first cut of this gate matched a list of dotted names (`time.monotonic`, …). A planted
`import time as _t; _t.monotonic()` walked straight past it, and the census still printed OK. So the
controls below plant each *evasion* rather than each function: an alias, a `from`-import, and a
different ambient module. What they defend is the matcher's shape, not its vocabulary.
"""

from __future__ import annotations

import ast

import reducer_purity as R


def _impurities(source: str) -> list[tuple[str, str]]:
    """`(call, severity)` for a one-class module, through the same helpers the census uses."""
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    aliases = R._aliases(tree)
    injected = R._injected(node)
    found = []
    for function in R._functions(node):
        deciding = R._deciding_calls(function)
        for child in ast.walk(function):
            if not isinstance(child, ast.Call):
                continue
            ambient = R._is_ambient(child, aliases)
            is_injected = (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "self"
                and child.func.attr in injected
            )
            if ambient or is_injected:
                call = R._dotted(child.func) if ambient else f"self.{child.func.attr}"
                found.append((call, "decides" if id(child) in deciding else "stamps"))
    return found


def test_a_plain_clock_read_is_ambient():
    assert _impurities(
        "import time\n\nclass R:\n    def go(self):\n        return time.monotonic()"
    ) == [("time.monotonic", "stamps")]


def test_an_aliased_module_does_not_evade_the_matcher():
    """The evasion that got past the first cut: rename the module and the dotted name no longer
    matches. Matching by module root is what closes it."""
    source = "import time as _t\n\nclass R:\n    def go(self):\n        return _t.monotonic()"
    assert _impurities(source) == [("_t.monotonic", "stamps")]


def test_a_from_import_does_not_evade_the_matcher():
    """The second evasion: `from time import monotonic` leaves no module in the call at all."""
    source = "from time import monotonic\n\nclass R:\n    def go(self):\n        return monotonic()"
    assert _impurities(source) == [("monotonic", "stamps")]


def test_a_different_ambient_module_is_covered_without_being_listed():
    """`random` is matched because it is an ambient *module*, not because a function was enumerated
    — which is the property that keeps the next stdlib clock covered."""
    source = "import random\n\nclass R:\n    def go(self):\n        return random.random()"
    assert _impurities(source) == [("random.random", "stamps")]


def test_an_injected_collaborator_call_is_impure():
    source = (
        "class R:\n"
        "    def __init__(self, clock):\n"
        "        self._clock = clock\n"
        "    def go(self):\n"
        "        return self._clock()\n"
    )
    assert _impurities(source) == [("self._clock", "stamps")]


def test_a_reading_inside_a_comparison_decides():
    source = (
        "class R:\n"
        "    def __init__(self, epoch):\n"
        "        self._epoch = epoch\n"
        "    def go(self, state):\n"
        "        return self._epoch() > state.seen\n"
    )
    assert _impurities(source) == [("self._epoch", "decides")]


def test_a_reading_bound_to_a_local_a_branch_reads_still_decides():
    """The same defect written over two lines. Missing it would make the severity split decoration:
    every real offender would just assign first."""
    source = (
        "class R:\n"
        "    def __init__(self, epoch):\n"
        "        self._epoch = epoch\n"
        "    def go(self, state):\n"
        "        seen = self._epoch()\n"
        "        if seen > state.seen:\n"
        "            return 1\n"
        "        return 0\n"
    )
    assert _impurities(source) == [("self._epoch", "decides")]


def test_a_pure_reducer_reports_nothing():
    """The negative control on the negative controls: the matcher must not fire on ordinary code,
    or every reducer would read as impure and the gate would be turned off."""
    source = (
        "from dataclasses import replace\n"
        "class R:\n"
        "    def go(self, state, event):\n"
        "        return replace(state, seen=event.seen)\n"
    )
    assert _impurities(source) == []


def test_the_live_census_finds_the_registered_reducers():
    """Guards the discovery half, and only that: if walking the reactor's route table ever returns
    nothing, every number is zero and the gate passes while measuring an empty set.

    `decides == 0` belongs to `poe reducer-purity`, not here — see the note in
    `test_port_probe_census.py`: this file runs earlier in `poe all`, so asserting the value here
    aborts the sequence before the gate's message is reached.
    """
    state = R.build()

    assert state["reducers"] >= 5, "the reactor registers at least one reducer per owner slot"
