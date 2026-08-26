"""Planted controls for the architecture map — the cycle classifier was wrong on its first cut.

It asked "does any runtime edge exist inside this component", which marks a ten-module annotation
cycle as real coupling because one member imports one peer for real. The edge is there; the loop is
not. So the controls below plant the *distinction*, not the example: a loop closed by runtime edges,
a loop closed only by annotations, and — the regression — a component holding a runtime edge that
closes nothing.

A map that reports coupling where there is none is worse than no map: it sends someone to break up
a package that was never coupled.
"""

from __future__ import annotations

import arch_map as A


def _kinds(graph: dict[str, list[str]]) -> list[list[str]]:
    """Components the raw graph reports, before any runtime/annotation classification."""
    return A._cycles(graph)


def test_a_loop_closed_by_runtime_edges_is_a_loop():
    assert _kinds({"a": ["b"], "b": ["a"]}) == [["a", "b"]]


def test_a_runtime_edge_that_closes_nothing_is_not_a_loop():
    """The regression. `subselect` imports two peers for real and neither imports back; a
    classifier that only asks "is there a runtime edge here" calls the whole component coupled."""
    assert (
        _kinds(
            {
                "subselect": ["subtitle_modes", "subtitle_providers"],
                "subtitle_modes": [],
                "subtitle_providers": [],
            }
        )
        == []
    )


def test_a_self_edge_is_not_a_component():
    """Tarjan yields a single node for a self-loop; reporting it as a cycle would flag every
    module that imports itself in a docstring example."""
    assert _kinds({"a": ["a"]}) == []


def test_type_only_and_deferred_imports_are_classified_apart(tmp_path):
    """The three edge kinds, from one file — what makes the annotation cycles free."""
    module = tmp_path / "m.py"
    module.write_text(
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "import saitenka.app.alpha\n"
        "if TYPE_CHECKING:\n"
        "    from saitenka.app import beta\n"
        "def go():\n"
        "    from saitenka.app import gamma\n"
        "    return gamma\n",
        encoding="utf-8",
    )

    kinds = A._edge_kinds(module, {"alpha", "beta", "gamma"})

    assert kinds == {"alpha": A._RUNTIME, "beta": A._ANNOTATION_ONLY, "gamma": A._DEFERRED}


def test_an_import_at_module_scope_stays_runtime_even_if_a_body_repeats_it(tmp_path):
    """Deferred is a property of the *only* import site. Written as `top - deferred` this name
    lands in no bucket at all and the edge vanishes from the map — an unclassified edge is not a
    missing cost, it is a missing row."""
    module = tmp_path / "m.py"
    module.write_text(
        "from saitenka.app import alpha\ndef go():\n    from saitenka.app import alpha\n    return alpha\n",
        encoding="utf-8",
    )

    assert A._edge_kinds(module, {"alpha"}) == {"alpha": A._RUNTIME}


def test_the_live_map_is_not_vacuous():
    """Guards the discovery halves. Each view derives from the live reactor or the real tree; if
    any stops resolving, it renders an empty table and the map reads as an architecture with no
    owners rather than as a broken tool."""
    state = A.build()

    assert state["static"]["modules"] > 100, "the import graph resolved almost nothing"
    assert len(state["ownership"]["owners"]) >= 5, "owner slots came back empty"
    assert state["commands"]["rows"], "the command table did not parse"
    assert state["seams"]["stateless"]["policies"], "no stateless policy found"
    assert state["seams"]["stateful"]["registered"], "no registered reducer found"


def test_every_stateless_policy_is_reported_as_stateless():
    """The seam view's claim, checked rather than asserted in prose: these are policies over a
    snapshot, which is *why* the mailbox is the wrong destination for them. A policy that starts
    threading state belongs in an owner slice, and this is where that shows up."""
    policies = A.build()["seams"]["stateless"]["policies"]

    threading = [p["module"] for p in policies if p["stateful"]]

    assert not threading, (
        f"policies now threading state: {threading}. A reducer with state belongs in an owner "
        "slice (`SliceReducer({name: reducer})`), not in the host-driven policy layer."
    )


def test_every_stateless_policy_reports_registration_and_bounded_capabilities():
    """The map reports the closed registry and the authority values used to assemble it."""
    stateless = A.seams_view()["stateless"]
    policies = {p["feature"] for p in stateless["policies"]}
    capabilities = stateless["ports"]

    assert stateless["seam"], "the seam exists; the view must name it"
    assert policies and policies == set(stateless["registered"])
    assert capabilities and all(capability["members"] for capability in capabilities)
    assert not [
        capability["port"] for capability in capabilities if capability["port"].endswith("Host")
    ]
