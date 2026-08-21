"""Planted controls for the port-probe census — both directions, because both were wrong once.

A first cut classified by the probed *name*: any attribute some class in `src/` happened to define
counted as debt, which made `entry.reading` and `f.lemma` debt and produced 52 rows of noise. The
fix was to resolve the receiver's type. Then `hasattr` on the class turned out to answer False for
an attribute `__init__` sets, so two real dead probes read as live capability checks.

So the controls pin the discriminator from both sides: a probe on a port that has the member is
dead, one on a port that does not is live, and an unresolvable receiver is neither.
"""

from __future__ import annotations

import ast

import port_probe_census as C


def _resolve(source: str, receiver_of: str = "go") -> str | None:
    """The class name the census resolves the probe's receiver to, in a one-module snippet."""
    tree = ast.parse(source)
    types = C._Types()
    types.visit(tree)
    for scope, klass, node in C._calls(tree):
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and scope
            and scope[-1] == receiver_of
        ):
            return C._receiver_type(node.args[0], types, scope, klass)
    return None


def test_a_self_attribute_resolves_through_the_constructor_parameter():
    """`self.ipc` is only a port because `__init__` took one — that link is the whole resolution."""
    source = (
        "class Reader:\n"
        "    def __init__(self, ipc: MpvIPC):\n"
        "        self.ipc = ipc\n"
        "    def go(self):\n"
        "        return getattr(self.ipc, 'command_async', None)\n"
    )
    assert _resolve(source) == "MpvIPC"


def test_an_optional_annotation_resolves_to_the_port_it_makes_optional():
    """`MpvIPC | None` is still a question about `MpvIPC`; resolving it to None would drop the row."""
    source = (
        "class Reader:\n"
        "    def __init__(self, ipc: MpvIPC | None):\n"
        "        self.ipc = ipc\n"
        "    def go(self):\n"
        "        return getattr(self.ipc, 'command_async', None)\n"
    )
    assert _resolve(source) == "MpvIPC"


def test_an_unannotated_parameter_stays_unresolved_rather_than_guessed():
    """The `renderer` case. Guessing here is what produced 52 rows of noise, so the census must
    report the receiver as unresolved instead of matching on the probed name."""
    source = "def go(renderer):\n    return getattr(renderer, 'ownership_state', None)\n"
    assert _resolve(source) is None


def test_any_and_object_name_no_port():
    """A receiver typed `Any` resolves to a class object at runtime, which would answer the probe's
    question with a shrug — `transport._api` read as a live capability check for that reason."""
    source = (
        "class T:\n"
        "    def __init__(self, api: Any):\n"
        "        self._api = api\n"
        "    def go(self):\n"
        "        return getattr(self._api, 'ERROR_MORE_DATA', -1)\n"
    )
    assert _resolve(source) is None


def test_a_lazy_slot_receiver_is_not_a_probe_at_all():
    """A thread-local's attribute is legitimately absent until first set, so the probe IS the read."""
    tail = C._LAZY_SLOT_RECEIVERS
    assert "_tls" in tail and "_local" in tail


def test_init_assigned_attributes_count_as_always_present():
    """`hasattr` on the class is False for these, which is how `ipc.connected_at` first read as a
    live capability check when every instance has it."""
    always = C._always_set()
    assert "connected_at" in always.get("MpvIPC", set())
    assert "_bytes_read" in always.get("MpvIPC", set())


def test_the_live_census_resolves_something_and_stays_clean():
    """Guards the discovery half: a census that resolved no receiver at all would report zero dead
    probes and pass, having measured nothing."""
    state = C.build()
    assert state["total"] > 0, "no getattr probes found in src — the sweep would be vacuous"
    resolved = state["total"] - state["unresolved"]
    assert resolved > 0, "no receiver resolved to a class — the discriminator is not running"
    assert state["dead"] == 0
