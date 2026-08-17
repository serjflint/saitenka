"""Pure, testable contracts for Saitenka's future session runtime."""

from saitenka.runtime.effects import (
    AsyncEffect,
    Effect,
    EffectError,
    EffectId,
    EffectOutcome,
    EmitDiagnostic,
    Owner,
    ScheduleTimer,
    StopSession,
    SubmitJob,
)
from saitenka.runtime.events import (
    CloseRequested,
    ConnectionReplaced,
    EffectFinished,
    EventEnvelope,
    EventOrigin,
    RawMpvEvent,
    RuntimeEvent,
    UserCommand,
)
from saitenka.runtime.mailbox import MailboxFull, SessionMailbox, TrafficClass
from saitenka.runtime.reactor import SessionReactor
from saitenka.runtime.timers import TimerScheduler

__all__ = [
    "AsyncEffect",
    "CloseRequested",
    "ConnectionReplaced",
    "Effect",
    "EffectError",
    "EffectFinished",
    "EffectId",
    "EffectOutcome",
    "EmitDiagnostic",
    "EventEnvelope",
    "EventOrigin",
    "MailboxFull",
    "Owner",
    "RawMpvEvent",
    "RuntimeEvent",
    "ScheduleTimer",
    "SessionMailbox",
    "SessionReactor",
    "StopSession",
    "SubmitJob",
    "TimerScheduler",
    "TrafficClass",
    "UserCommand",
]
