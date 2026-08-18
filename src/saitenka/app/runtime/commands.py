"""Pure script-message policy and the temporary legacy execution adapter."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from saitenka.app.bindings import (
    ANALYSIS_MSG,
    ANNOTATION_MSG,
    BOOKMARK_MSG,
    CLICK_MSG,
    COPY_CLICK_MSG,
    COPY_LINE_MSG,
    COPY_MSG,
    HELP_CLOSE_MSG,
    HELP_NEXT_MSG,
    HELP_PREV_MSG,
    HELP_TOGGLE_MSG,
    HOVER_PAUSE_MSG,
    KANJI_MSG,
    MINE_ALL_MSG,
    MINE_MSG,
    MINE_VIDEO_MSG,
    OVERLAY_TOGGLE_MSG,
    PREVIEW_CLOSE_MSG,
    PREVIEW_MSG,
    PROFILE_CYCLE_MSG,
    SCROLL_DOWN_MSG,
    SCROLL_UP_MSG,
    SIDEBAR_MSG,
    SPEAK_MSG,
    SUB_ANCHOR_MSG,
    SUB_NEXT_MSG,
    SUB_PICKER_MSG,
    SUB_PREV_MSG,
    SUB_REPLAY_MSG,
    SUBTITLE_LANGUAGE_MSG,
    SUBTITLE_MARK_JP_MSG,
    SUBTITLE_RETRY_MSG,
    TIP_CLOSE_MSG,
    TIP_DOWN_MSG,
    TIP_UP_MSG,
    TRANS_MSG,
)
from saitenka.runtime import (
    CommandHandled,
    CommandOutcome,
    CommandReason,
    Owner,
    UserCommand,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping


class CueCommandState(StrEnum):
    NEVER_INSTALLED = "never-installed"
    ACTIVE = "active"
    RETIRED_AFTER_ACTIVE = "retired-after-active"


CommandRejection = CommandReason

#: Route label for a command that no longer has a temporary binding to delete.
MIGRATED_ROUTE = "migrated"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    owner: Owner
    requires_cue: bool
    allowed_while_help_open: bool = False


@dataclass(frozen=True, slots=True)
class CommandIntent:
    name: str
    args: tuple[object, ...]
    owner: Owner
    command_id: int | None = None


@dataclass(frozen=True, slots=True)
class CommandDecision:
    intent: CommandIntent | None = None
    rejection: CommandRejection | None = None
    owner: Owner | None = None

    def __post_init__(self) -> None:
        if (self.intent is None) == (self.rejection is None):
            raise ValueError("command decision requires exactly one result")


@dataclass(frozen=True, slots=True)
class LegacyCommandBinding:
    handler: Callable[[], None]
    deletion_owner: str


@dataclass(frozen=True, slots=True)
class CommandExecution:
    name: str
    owner: Owner | None
    outcome: CommandOutcome
    command_id: int | None = None
    rejection: CommandRejection | None = None
    error_type: str | None = None

    def event(self) -> CommandHandled:
        reason = self.rejection
        if self.outcome == CommandOutcome.FAILED:
            reason = CommandReason.INTERNAL
        elif self.outcome == CommandOutcome.SUPPRESSED:
            reason = CommandReason.LEGACY_REPEAT
        return CommandHandled(self.name, self.owner, self.outcome, self.command_id, reason)

    def coalesced_events(self, command_ids: tuple[int, ...]) -> tuple[CommandHandled, ...]:
        primary = self.event()
        if self.outcome == CommandOutcome.REJECTED:
            return tuple(
                CommandHandled(
                    primary.name,
                    primary.owner,
                    primary.outcome,
                    command_id,
                    primary.reason,
                )
                for command_id in command_ids
            )
        return tuple(
            CommandHandled(
                primary.name,
                primary.owner,
                CommandOutcome.SUPPRESSED,
                command_id,
                CommandReason.COALESCED,
            )
            for command_id in command_ids
        )


class LegacyPickerRepeatGuard:
    """Drain-local compatibility guard deleted with the tick driver."""

    def __init__(self, picker_name: str = SUB_PICKER_MSG) -> None:
        self._picker_name = picker_name
        self._previous: str | None = None

    def inspect(self, command: UserCommand) -> CommandExecution | None:
        previous, self._previous = self._previous, command.name
        if command.name != self._picker_name or previous != self._picker_name:
            return None
        return CommandExecution(
            command.name,
            Owner.INTERACTION,
            CommandOutcome.SUPPRESSED,
            command_id=command.command_id,
        )

    def separate(self) -> None:
        self._previous = None


_CUE_INDEPENDENT = frozenset(
    {
        HELP_TOGGLE_MSG,
        HELP_PREV_MSG,
        HELP_NEXT_MSG,
        HELP_CLOSE_MSG,
        OVERLAY_TOGGLE_MSG,
        SUBTITLE_LANGUAGE_MSG,
        SUBTITLE_MARK_JP_MSG,
        SUBTITLE_RETRY_MSG,
        PROFILE_CYCLE_MSG,
        HOVER_PAUSE_MSG,
        SIDEBAR_MSG,
        SUB_PICKER_MSG,
        ANALYSIS_MSG,
        ANNOTATION_MSG,
        PREVIEW_CLOSE_MSG,
        SCROLL_UP_MSG,
        SCROLL_DOWN_MSG,
        TIP_CLOSE_MSG,
        TIP_UP_MSG,
        TIP_DOWN_MSG,
        SUB_ANCHOR_MSG,
    }
)
_HELP_COMMANDS = frozenset(
    {HELP_TOGGLE_MSG, HELP_PREV_MSG, HELP_NEXT_MSG, HELP_CLOSE_MSG, SCROLL_UP_MSG, SCROLL_DOWN_MSG}
)

_OWNER_COMMANDS: tuple[tuple[Owner, tuple[str, ...]], ...] = (
    (
        Owner.SESSION,
        (
            OVERLAY_TOGGLE_MSG,
            PROFILE_CYCLE_MSG,
            HOVER_PAUSE_MSG,
            HELP_TOGGLE_MSG,
            HELP_PREV_MSG,
            HELP_NEXT_MSG,
            HELP_CLOSE_MSG,
        ),
    ),
    (
        Owner.PLAYBACK,
        (
            SUBTITLE_LANGUAGE_MSG,
            SUBTITLE_MARK_JP_MSG,
            SUBTITLE_RETRY_MSG,
        ),
    ),
    (
        Owner.SUBTITLE,
        (
            TRANS_MSG,
            ANNOTATION_MSG,
            COPY_LINE_MSG,
            SUB_PREV_MSG,
            SUB_NEXT_MSG,
            SUB_REPLAY_MSG,
            SUB_ANCHOR_MSG,
        ),
    ),
    (
        Owner.INTERACTION,
        (
            MINE_MSG,
            MINE_VIDEO_MSG,
            MINE_ALL_MSG,
            BOOKMARK_MSG,
            SIDEBAR_MSG,
            ANALYSIS_MSG,
            PREVIEW_MSG,
            PREVIEW_CLOSE_MSG,
            SCROLL_UP_MSG,
            SCROLL_DOWN_MSG,
            SPEAK_MSG,
            COPY_MSG,
            COPY_CLICK_MSG,
            CLICK_MSG,
            KANJI_MSG,
            TIP_UP_MSG,
            TIP_DOWN_MSG,
            TIP_CLOSE_MSG,
            SUB_PICKER_MSG,
        ),
    ),
)

COMMAND_SPECS = tuple(
    CommandSpec(
        name,
        owner,
        requires_cue=name not in _CUE_INDEPENDENT,
        allowed_while_help_open=name in _HELP_COMMANDS,
    )
    for owner, names in _OWNER_COMMANDS
    for name in names
)


class CommandPolicy:
    """Closed name-to-owner routing plus cue/help eligibility; performs no I/O."""

    def __init__(self, specs: Iterable[CommandSpec] = COMMAND_SPECS) -> None:
        self._specs: dict[str, CommandSpec] = {}
        for spec in specs:
            if not spec.name:
                raise ValueError("command names must not be empty")
            if spec.name in self._specs:
                raise ValueError(f"command spec already registered: {spec.name}")
            self._specs[spec.name] = spec

    @property
    def specs(self) -> tuple[CommandSpec, ...]:
        return tuple(self._specs.values())

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def decide(
        self,
        command: UserCommand,
        *,
        cue_state: CueCommandState,
        help_open: bool,
    ) -> CommandDecision:
        if not command.name:
            return CommandDecision(rejection=CommandRejection.MALFORMED)
        spec = self._specs.get(command.name)
        if spec is None:
            return CommandDecision(rejection=CommandRejection.UNKNOWN)
        if help_open and not spec.allowed_while_help_open:
            return CommandDecision(rejection=CommandRejection.HELP_MODAL, owner=spec.owner)
        if spec.requires_cue and cue_state == CueCommandState.RETIRED_AFTER_ACTIVE:
            return CommandDecision(rejection=CommandRejection.CUE_RETIRED, owner=spec.owner)
        return CommandDecision(
            intent=CommandIntent(command.name, command.args, spec.owner, command.command_id)
        )


class LegacyCommandExecutor:
    """Temporary synchronous adapter from accepted intents to bound feature actions.

    A name carried by `reducers` is already migrated: its decision is a pure reducer and it holds
    no `LegacyCommandBinding` row. Reducer routes take precedence, so a migrated command cannot
    keep a compatibility handler alive behind it.
    """

    def __init__(
        self,
        bindings: Mapping[str, LegacyCommandBinding],
        *,
        policy: CommandPolicy | None = None,
        reducers: Mapping[str, Callable[[], None]] | None = None,
    ) -> None:
        self.policy = policy or CommandPolicy()
        self._reducers = dict(reducers or {})
        unknown = (frozenset(bindings) | frozenset(self._reducers)) - self.policy.names()
        if unknown:
            raise ValueError(f"legacy bindings have no command spec: {sorted(unknown)!r}")
        overlap = frozenset(bindings) & frozenset(self._reducers)
        if overlap:
            raise ValueError(f"migrated commands must not keep a binding: {sorted(overlap)!r}")
        self._bindings = dict(bindings)

    def names(self) -> frozenset[str]:
        return self.policy.names()

    @property
    def bindings(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (name, binding.deletion_owner) for name, binding in sorted(self._bindings.items())
        )

    @property
    def migrated(self) -> frozenset[str]:
        """Commands whose decision is a reducer. These hold no temporary binding row."""
        return frozenset(self._reducers)

    def route(self, name: str) -> str:
        """`migrated`, or the deletion owner of the temporary binding still behind the name."""
        if name in self._reducers:
            return MIGRATED_ROUTE
        binding = self._bindings.get(name)
        return binding.deletion_owner if binding is not None else "unbound"

    def dispatch(
        self,
        command: UserCommand,
        *,
        cue_state: CueCommandState,
        help_open: bool,
    ) -> CommandExecution:
        decision = self.policy.decide(command, cue_state=cue_state, help_open=help_open)
        if decision.rejection is not None:
            return CommandExecution(
                command.name,
                decision.owner,
                CommandOutcome.REJECTED,
                command_id=command.command_id,
                rejection=decision.rejection,
            )
        intent = decision.intent
        assert intent is not None
        handler = self._reducers.get(intent.name)
        if handler is None:
            binding = self._bindings.get(intent.name)
            handler = binding.handler if binding is not None else None
        if handler is None:
            return CommandExecution(
                intent.name,
                intent.owner,
                CommandOutcome.UNBOUND,
                command_id=intent.command_id,
            )
        try:
            handler()
        except Exception as error:  # noqa: BLE001  # failure is a typed terminal outcome
            return CommandExecution(
                intent.name,
                intent.owner,
                CommandOutcome.FAILED,
                command_id=intent.command_id,
                error_type=type(error).__name__,
            )
        return CommandExecution(
            intent.name,
            intent.owner,
            CommandOutcome.EXECUTED,
            command_id=intent.command_id,
        )
