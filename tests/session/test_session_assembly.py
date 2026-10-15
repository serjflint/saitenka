from __future__ import annotations

import util

from saitenka.app.config import ReaderOptions
from saitenka.app.feature_bindings import (
    ordered_stateful_bindings,
)
from saitenka.app.features.analysis.analysis_controller import AnalysisController
from saitenka.app.session.assembly import build_session_assembly
from saitenka.runtime import Owner


def test_assembly_derives_help_inventory_from_installed_command_rows():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)
    messages = [row.message for row in assembly.commands]

    assert assembly.features == frozenset(
        {
            "card-preview",
            "copy-pulse",
            "help",
            "hover",
            "hover-pause",
            "hovered-word",
            "sidebar",
            "subtitle-picker",
            "tooltip-navigation",
        }
    )
    assert {row.runtime_owner for row in assembly.commands} == {Owner.SESSION}
    assert all(row.endpoint.owner is assembly.help for row in assembly.commands)
    assert len(messages) == len(set(messages))
    assert {row.runtime_owner for row in assembly.stateful} == {
        plan.owner for plan in assembly.owner_plans
    }
    for plan in assembly.owner_plans:
        owned = tuple(row for row in assembly.stateful if row.runtime_owner is plan.owner)
        assert tuple(binding.key for binding in ordered_stateful_bindings(plan, owned)) == (
            plan.feature_order
        )


def test_assembly_constructs_surface_owners_from_registered_state_factories():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)
    bindings = {binding.feature: binding for binding in assembly.stateful}

    assert assembly.picker.store.current == bindings["subtitle-picker"].initial().picker
    assert assembly.sidebar.store.current == bindings["sidebar"].initial().sidebar
    assert assembly.preview.store.current == bindings["card-preview"].initial().preview
    assert assembly.picker.surface_binding().state_of() is assembly.picker.state
    assert assembly.sidebar.surface_binding().state_of() is assembly.sidebar.state
    assert assembly.preview.surface_binding().state_of() is assembly.preview.state


def test_assembly_constructs_the_analysis_owner_without_session_facts():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

    assert isinstance(assembly.analysis, AnalysisController)
    assert assembly.analysis.open is False
    assert assembly.analysis.result is None
