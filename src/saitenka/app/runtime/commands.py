"""Pure script-message policy, and the executor that runs the action bound to a command."""

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
    LEGACY_RENDERER_MSG,
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


def merge_command_handlers(
    *groups: Mapping[str, Callable[[], object]],
) -> dict[str, Callable[[], object]]:
    """Join independently closed command families without silent replacement."""
    merged: dict[str, Callable[[], object]] = {}
    for group in groups:
        duplicate = merged.keys() & group.keys()
        if duplicate:
            raise ValueError(f"command handler already registered: {sorted(duplicate)!r}")
        merged.update(group)
    return merged


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
            reason = CommandReason.COALESCED
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


_CUE_INDEPENDENT = frozenset(
    {
        OVERLAY_TOGGLE_MSG,
        SUBTITLE_LANGUAGE_MSG,
        SUBTITLE_MARK_JP_MSG,
        SUBTITLE_RETRY_MSG,
        LEGACY_RENDERER_MSG,
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
        HELP_TOGGLE_MSG,
        HELP_PREV_MSG,
        HELP_NEXT_MSG,
        HELP_CLOSE_MSG,
    }
)
_HELP_COMMANDS = frozenset(
    {
        HELP_TOGGLE_MSG,
        HELP_PREV_MSG,
        HELP_NEXT_MSG,
        HELP_CLOSE_MSG,
        SCROLL_UP_MSG,
        SCROLL_DOWN_MSG,
    }
)

_OWNER_COMMANDS: tuple[tuple[Owner, tuple[str, ...]], ...] = (
    (
        Owner.SESSION,
        (
            OVERLAY_TOGGLE_MSG,
            PROFILE_CYCLE_MSG,
            HOVER_PAUSE_MSG,
            LEGACY_RENDERER_MSG,
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


class CommandExecutor:
    """Decide a command against the policy, then run the action bound to it.

    It used to carry a second map beside this one — `LegacyCommandBinding`, a temporary handler
    with the work package that would delete it — because a command whose decision was still
    imperative had nowhere else to live. Every command's decision is a reducer now, so that map
    was empty at every construction in `src/` and the machinery around it (the deletion owner, the
    "migrated commands must not keep a binding" guard, the `route`/`bindings` meters) was reporting
    on a migration that had finished.
    """

    def __init__(
        self,
        handlers: Mapping[str, Callable[[], object]],
        *,
        policy: CommandPolicy | None = None,
    ) -> None:
        self.policy = policy or CommandPolicy()
        unknown = frozenset(handlers) - self.policy.names()
        if unknown:
            raise ValueError(f"command handlers have no command spec: {sorted(unknown)!r}")
        self._handlers = dict(handlers)

    def names(self) -> frozenset[str]:
        return self.policy.names()

    @property
    def routed(self) -> frozenset[str]:
        """Spec'd names that actually resolve to an action. A spec outside this set dispatches to
        `UNBOUND` — a key that is documented, accepted by the policy, and does nothing."""
        return frozenset(self._handlers)

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
        handler = self._handlers.get(intent.name)
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
