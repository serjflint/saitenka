"""Closed, inspectable assembly rows for one study session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app.bindings import HELP_CLOSE_MSG, HELP_NEXT_MSG, HELP_PREV_MSG, HELP_TOGGLE_MSG
from saitenka.app.feature_bindings import (
    HELP_STATEFUL_BINDING,
    INTERACTION_OWNER_PLAN,
    INTERACTION_STATEFUL_BINDINGS,
    InstalledStatefulBinding,
    OwnerPlan,
    ordered_stateful_bindings,
)
from saitenka.app.features.analysis.analysis_controller import AnalysisController
from saitenka.app.features.help.help_controller import (
    HelpController,
    ScreenState,
    TooltipKeyContext,
)
from saitenka.app.features.picker.picker_controller import PickerController
from saitenka.app.features.preview.preview_controller import PreviewController
from saitenka.app.features.sidebar.sidebar_controller import SidebarController
from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
from saitenka.app.runtime import CommandSpec
from saitenka.mpvio.osd import Overlay
from saitenka.runtime.effects import Owner
from saitenka.runtime.help import HelpCommand

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.config import ReaderOptions
    from saitenka.mpvio.ipc import MpvIPC


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


def _validate_command_registrations(
    commands: tuple[CommandRegistration, ...],
    help_owner: HelpController,
) -> None:
    messages: set[str] = set()
    features: dict[str, tuple[Owner, object]] = {}
    for row in commands:
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
    if help_identity is None or help_identity[1] is not help_owner:
        raise ValueError("help commands must terminate at the installed help owner")


def _validate_stateful_registrations(
    stateful: tuple[InstalledStatefulBinding, ...],
    owner_plans: tuple[OwnerPlan, ...],
) -> None:
    plans: dict[Owner, OwnerPlan] = {}
    for plan in owner_plans:
        if plan.owner in plans:
            raise ValueError(f"owner plan already registered: {plan.owner.value}")
        plans[plan.owner] = plan
    if {row.runtime_owner for row in stateful} != set(plans):
        raise ValueError("owner plans and stateful bindings disagree")
    for plan in plans.values():
        owned = tuple(row for row in stateful if row.runtime_owner is plan.owner)
        ordered_stateful_bindings(plan, owned)


@dataclass(frozen=True, slots=True)
class SessionAssembly:
    overlay: Overlay
    surfaces: LifecycleSurfaces
    screen: ScreenState
    tooltip_keys: TooltipKeyContext
    help: HelpController
    analysis: AnalysisController
    picker: PickerController
    sidebar: SidebarController
    preview: PreviewController
    commands: tuple[CommandRegistration, ...]
    stateful: tuple[InstalledStatefulBinding, ...]
    owner_plans: tuple[OwnerPlan, ...]

    def __post_init__(self) -> None:
        _validate_command_registrations(self.commands, self.help)
        _validate_stateful_registrations(self.stateful, self.owner_plans)

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
    analysis_owner = AnalysisController(
        ipc,
        surfaces,
        screen,
        options.keys,
        ui_scale=ui_scale,
    )
    picker_owner = PickerController(
        ipc,
        surfaces,
        screen,
        options.keys,
        ui_scale=ui_scale,
    )
    sidebar_owner = SidebarController(ipc)
    preview_owner = PreviewController(ipc)
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
        overlay=resolved_overlay,
        surfaces=surfaces,
        screen=screen,
        tooltip_keys=tooltip_keys,
        help=help_owner,
        analysis=analysis_owner,
        picker=picker_owner,
        sidebar=sidebar_owner,
        preview=preview_owner,
        commands=commands,
        stateful=INTERACTION_STATEFUL_BINDINGS,
        owner_plans=(INTERACTION_OWNER_PLAN,),
    )
