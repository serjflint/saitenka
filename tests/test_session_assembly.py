from __future__ import annotations

from dataclasses import replace

import pytest
import util

from saitenka.app.config import ReaderOptions
from saitenka.app.feature_bindings import (
    INTERACTION_OWNER_PLAN,
    ordered_stateful_bindings,
)
from saitenka.app.session_assembly import CommandRegistration, build_session_assembly
from saitenka.runtime import Owner


def test_assembly_derives_help_inventory_from_installed_command_rows():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

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
    assert (
        tuple(
            binding.key
            for binding in ordered_stateful_bindings(INTERACTION_OWNER_PLAN, assembly.stateful)
        )
        == INTERACTION_OWNER_PLAN.feature_order
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


def test_assembly_rejects_duplicate_command_messages():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

    with pytest.raises(ValueError, match="already registered"):
        replace(assembly, commands=(*assembly.commands, assembly.commands[0]))


def test_assembly_rejects_one_feature_resolving_to_two_owners():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

    class ForeignEndpoint:
        owner = object()

        def run(self) -> None:
            pass

    conflicting = CommandRegistration(
        "help",
        Owner.SESSION,
        "saitenka-help-conflict",
        ForeignEndpoint(),
        requires_cue=False,
        allowed_while_help_open=True,
    )
    with pytest.raises(ValueError, match="ownership disagrees"):
        replace(assembly, commands=(*assembly.commands, conflicting))


def test_assembly_rejects_duplicate_stateful_keys():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

    with pytest.raises(ValueError, match="already registered"):
        replace(assembly, stateful=(*assembly.stateful, assembly.stateful[0]))


def test_owner_plan_rejects_a_missing_stateful_binding():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

    with pytest.raises(ValueError, match="disagree"):
        replace(assembly, stateful=assembly.stateful[:-1])


def test_owner_plan_rejects_an_event_without_a_declared_consumer():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)
    first = assembly.stateful[0]

    with pytest.raises(ValueError, match="accepted event vocabulary"):
        replace(assembly, stateful=(replace(first, accepted_events=()), *assembly.stateful[1:]))
