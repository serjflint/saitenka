from __future__ import annotations

from dataclasses import replace

import pytest
import util

from saitenka.app.config import ReaderOptions
from saitenka.app.session_assembly import CommandRegistration, build_session_assembly
from saitenka.runtime import Owner


def test_assembly_derives_help_inventory_from_installed_command_rows():
    assembly = build_session_assembly(util.FakeIPC(), ReaderOptions(), runtime_submit=None)

    assert assembly.features == frozenset({"help"})
    assert {row.runtime_owner for row in assembly.commands} == {Owner.SESSION}
    assert all(row.endpoint.owner is assembly.help for row in assembly.commands)
    assert assembly.stateful[0].feature == "help"


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

    with pytest.raises(ValueError, match="stateful feature keys"):
        replace(assembly, stateful=(*assembly.stateful, assembly.stateful[0]))
