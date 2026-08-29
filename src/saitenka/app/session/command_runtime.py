"""Owner-thread command admission, execution, and input installation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.bindings import GLOBAL_SECTION, active_bindings, section_contents
from saitenka.app.mpv_egress import send_correlated
from saitenka.app.runtime import (
    COMMAND_SPECS,
    CommandExecutor,
    CommandOutcome,
    CommandPolicy,
    merge_command_handlers,
)
from saitenka.runtime import (
    CommandHandled,
    CommandReason,
    Owner,
    UserCommand,
)
from saitenka.runtime.effects import RunUserCommand

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from saitenka.app.config import KeyOptions
    from saitenka.app.features.annotation.annotation_controller import CueAnnotationController
    from saitenka.app.features.help.help_controller import HelpController
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.interaction.mouse_capture import MouseCapture
    from saitenka.app.runtime import CommandSpec
    from saitenka.app.session.cue_coordinator import CueCoordinator
    from saitenka.app.session.stateless import StatelessCommandGraph
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.connection import ConnectionStore

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CommandRuntimePorts:
    ipc: MpvIPC
    keys: KeyOptions
    contributed_handlers: Mapping[str, Callable[[], object]]
    contributed_specs: tuple[CommandSpec, ...]
    stateless: StatelessCommandGraph
    mining: MiningController
    connection: ConnectionStore
    cue: CueCoordinator
    annotation: CueAnnotationController
    help: HelpController
    mouse: MouseCapture


class CommandRuntime:
    """The single owner-thread route from script-message to a terminal outcome."""

    def __init__(self, ports: CommandRuntimePorts) -> None:
        self._ports = ports
        handlers = merge_command_handlers(
            ports.contributed_handlers,
            ports.stateless.handlers(),
        )
        contributed_names = {spec.name for spec in ports.contributed_specs}
        built_in_specs = tuple(spec for spec in COMMAND_SPECS if spec.name not in contributed_names)
        self._commands = CommandExecutor(
            handlers,
            policy=CommandPolicy((*built_in_specs, *ports.contributed_specs)),
        )

    def install_input(self) -> None:
        ports = self._ports
        bindings = [
            row for row in active_bindings(ports.keys, "global") if row.spec.message is not None
        ]
        contents = section_contents(bindings)
        if contents:
            send_correlated(
                ports.ipc,
                "define-global-section",
                "define-section",
                GLOBAL_SECTION,
                contents,
                "force",
                owner=Owner.INTERACTION,
            )
            send_correlated(
                ports.ipc,
                "enable-global-section",
                "enable-section",
                GLOBAL_SECTION,
                owner=Owner.INTERACTION,
            )
        log.info(
            "registered %d global keybinds (anki=%s)",
            len(bindings),
            ports.mining.configured,
        )
        ports.mouse.define(active_bindings(ports.keys, "mouse"))

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        return self._commands.policy.specs

    @property
    def routed(self) -> frozenset[str]:
        return self._commands.routed

    def names(self) -> frozenset[str]:
        return self._commands.names()

    def run_effect(self, effect: object) -> None:
        assert isinstance(effect, RunUserCommand)
        self.perform(effect.command)

    def perform(self, command: UserCommand) -> None:
        if not self._ports.connection.current.ready:
            self._publish(
                CommandHandled(
                    command.name,
                    None,
                    CommandOutcome.REJECTED,
                    command_id=command.command_id,
                    reason=CommandReason.DISCONNECTED,
                )
            )
            return
        self.handle(command)

    def handle(self, command: str | UserCommand) -> None:
        if isinstance(command, str):
            command = UserCommand(command)
        log.debug("script-message: %s", command.name)
        result = self._commands.dispatch(
            command,
            cue_state=self._ports.cue.command_state(retired=self._ports.annotation.view.retired),
            help_open=self._ports.help.state.open,
        )
        self._publish(result.event())
        for coalesced in result.coalesced_events(command.coalesced_ids):
            self._publish(coalesced)
        if result.outcome == CommandOutcome.REJECTED:
            log.debug("script-message rejected (%s): %s", result.rejection, command.name)
        elif result.outcome == CommandOutcome.UNBOUND:
            log.error("script-message has no registered binding: %s", command.name)
        elif result.outcome == CommandOutcome.FAILED:
            log.error("script-message failed (%s): %s", result.error_type, command.name)

    def _publish(self, event: CommandHandled) -> None:
        self._ports.ipc.publish_legacy_command_outcome(event)
