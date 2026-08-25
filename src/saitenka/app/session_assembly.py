"""Closed, inspectable assembly rows for one study session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app.bindings import HELP_CLOSE_MSG, HELP_NEXT_MSG, HELP_PREV_MSG, HELP_TOGGLE_MSG
from saitenka.app.feature_bindings import HELP_STATEFUL_BINDING, StatefulBinding
from saitenka.app.help_controller import HelpController, ScreenState, TooltipKeyContext
from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
from saitenka.app.runtime import CommandSpec
from saitenka.mpvio.osd import Overlay
from saitenka.runtime.effects import Owner
from saitenka.runtime.help import HelpCommand

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.config import ReaderOptions
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.interaction_slice import HelpFeature, HelpReducer, HelpStore


class CommandEndpoint(Protocol):
    @property
    def owner(self) -> object: ...

    def run(self) -> None: ...


@dataclass(frozen=True, slots=True)
class HelpEndpoint:
    owner: HelpController
    command: HelpCommand

    def run(self) -> None:
        self.owner.run(self.command)


@dataclass(frozen=True, slots=True)
class CommandRegistration:
    feature: str
    runtime_owner: Owner
    message: str
    endpoint: CommandEndpoint
    requires_cue: bool
    allowed_while_help_open: bool

    def policy_spec(self) -> CommandSpec:
        return CommandSpec(
            self.message,
            self.runtime_owner,
            requires_cue=self.requires_cue,
            allowed_while_help_open=self.allowed_while_help_open,
        )


@dataclass(frozen=True, slots=True)
class SessionAssembly:
    overlay: Overlay
    surfaces: LifecycleSurfaces
    screen: ScreenState
    tooltip_keys: TooltipKeyContext
    help: HelpController
    commands: tuple[CommandRegistration, ...]
    stateful: tuple[StatefulBinding[HelpFeature, HelpReducer, HelpStore], ...]

    def __post_init__(self) -> None:
        messages: set[str] = set()
        features: dict[str, tuple[Owner, object]] = {}
        for row in self.commands:
            if not row.message:
                raise ValueError("command message must not be empty")
            if row.message in messages:
                raise ValueError(f"command already registered: {row.message}")
            messages.add(row.message)
            identity = (row.runtime_owner, row.endpoint.owner)
            previous = features.setdefault(row.feature, identity)
            if previous[0] is not row.runtime_owner or previous[1] is not row.endpoint.owner:
                raise ValueError(f"feature ownership disagrees across rows: {row.feature}")
        help_identity = features.get("help")
        if help_identity is None or help_identity[1] is not self.help:
            raise ValueError("help commands must terminate at the installed help owner")
        stateful_keys = [row.key for row in self.stateful]
        if len(stateful_keys) != len(set(stateful_keys)):
            raise ValueError("stateful feature keys must be unique")

    @property
    def features(self) -> frozenset[str]:
        return frozenset(
            [*(row.feature for row in self.commands), *(row.feature for row in self.stateful)]
        )

    def command_handlers(self) -> dict[str, Callable[[], None]]:
        return {row.message: row.endpoint.run for row in self.commands}

    def command_specs(self) -> tuple[CommandSpec, ...]:
        return tuple(row.policy_spec() for row in self.commands)


def build_session_assembly(
    ipc: MpvIPC,
    options: ReaderOptions,
    *,
    runtime_submit: Callable[..., object] | None,
    overlay: Overlay | None = None,
) -> SessionAssembly:
    resolved_overlay = overlay or Overlay(
        ipc,
        id_base=options.overlay_id_base,
        runtime_submit=runtime_submit,
    )
    surfaces = LifecycleSurfaces(resolved_overlay)
    screen = ScreenState()
    tooltip_keys = TooltipKeyContext()
    ui_scale = max(0.75, min(2.0, float(options.panels.scale)))
    help_owner = HelpController(
        ipc,
        surfaces,
        options.keys,
        screen,
        tooltip_keys,
        HELP_STATEFUL_BINDING.store(ipc),
        ui_scale=ui_scale,
    )
    commands = tuple(
        CommandRegistration(
            "help",
            Owner.SESSION,
            message,
            HelpEndpoint(help_owner, command),
            requires_cue=False,
            allowed_while_help_open=True,
        )
        for message, command in (
            (HELP_TOGGLE_MSG, HelpCommand.TOGGLE),
            (HELP_PREV_MSG, HelpCommand.PREVIOUS),
            (HELP_NEXT_MSG, HelpCommand.NEXT),
            (HELP_CLOSE_MSG, HelpCommand.CLOSE),
        )
    )
    return SessionAssembly(
        resolved_overlay,
        surfaces,
        screen,
        tooltip_keys,
        help_owner,
        commands,
        (HELP_STATEFUL_BINDING,),
    )
