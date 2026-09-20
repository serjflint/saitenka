"""Bounded accounting for one subtitle appearance; timestamps are supplied by the caller."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ColorWrite:
    occurrence: int
    serial: int
    device: str
    tokens: frozenset[int]


@dataclass(frozen=True, slots=True)
class ColorOutcome:
    occurrence: int
    kind: str
    status: str
    reason: str
    requested: int | None
    permitted: int
    submitted: int
    acknowledged: int
    first_ms: float | None
    complete_ms: float | None
    late: bool
    withdrawals: int
    eligibility_withdrawn: int
    elapsed_ms: float


@dataclass(slots=True)
class ColorAccounting:
    """A single occurrence and bounded per-device writes, independent of rendering decisions."""

    occurrence: int
    kind: str
    started: float
    budget_ms: float
    requested: frozenset[int] | None = None
    permitted: set[int] = field(default_factory=set)
    current: frozenset[int] = frozenset()
    submitted: set[int] = field(default_factory=set)
    acknowledged: dict[int, float] = field(default_factory=dict)
    visible: dict[str, frozenset[int]] = field(default_factory=dict)
    writes: dict[str, ColorWrite] = field(default_factory=dict)
    late: bool = False
    withdrawals: int = 0
    eligibility_withdrawn: set[int] = field(default_factory=set)
    failed: bool = False
    closed: bool = False
    qualification_known: bool = False
    _serial: int = 0

    def qualify(
        self, requested: frozenset[int], permitted: frozenset[int] | None, now: float
    ) -> None:
        if self.closed:
            return
        self.check_deadline(now)
        self.requested = requested if self.requested is None else self.requested | requested
        if permitted is None:
            return
        self.qualification_known = True
        self.permitted.update(permitted)
        self.eligibility_withdrawn.update(self.current - permitted)
        self.current = permitted
        self.check_deadline(now)

    def check_deadline(self, now: float) -> bool:
        """Return whether this call first discovers a deadline miss."""
        missed = (
            not self.closed
            and not self.late
            and (now - self.started) * 1000 > self.budget_ms
            and bool(self.current - self.acknowledged.keys())
        )
        self.late |= missed
        return missed

    def submit(self, device: str, tokens: frozenset[int]) -> ColorWrite:
        self._serial += 1
        write = ColorWrite(self.occurrence, self._serial, device, tokens)
        if not self.closed:
            self.writes[device] = write
            self.submitted.update(tokens)
        return write

    def corrected_target(self) -> ColorAccounting:
        return ColorAccounting(
            self.occurrence,
            self.kind,
            self.started,
            self.budget_ms,
            late=self.late,
            withdrawals=self.withdrawals,
            _serial=self._serial,
        )

    def settle(self, write: ColorWrite, now: float, *, accepted: bool) -> bool:
        """Only the latest matching write can change coverage or settle a wait."""
        if self.closed or self.writes.get(write.device) != write:
            return False
        self.check_deadline(now)
        del self.writes[write.device]
        if not accepted:
            self.failed = True
            return False
        before = self.coverage
        self.visible[write.device] = write.tokens
        if before - self.coverage:
            self.withdrawals += 1
        for token in write.tokens & self.permitted:
            self.acknowledged.setdefault(token, now)
        return True

    @property
    def coverage(self) -> frozenset[int]:
        return frozenset().union(*self.visible.values())

    def retire(self, now: float, reason: str) -> ColorOutcome | None:
        if self.closed:
            return None
        self.check_deadline(now)
        self.closed = True
        return self.snapshot(now, reason)

    def snapshot(self, now: float, reason: str = "active") -> ColorOutcome:
        acked = self.permitted & self.acknowledged.keys()
        first = min(self.acknowledged.values(), default=None)
        complete = (
            max(self.acknowledged.values()) if self.permitted and acked == self.permitted else None
        )
        if self.requested is None:
            status = "unknown"
        elif not self.requested:
            status = "no-color-requested"
        elif not self.permitted and not self.qualification_known:
            status = "unknown"
        elif not self.permitted:
            status = "policy-suppressed"
        elif complete is not None:
            status = "complete"
        elif acked:
            status = "partial"
        elif self.failed:
            status = "failed"
        else:
            status = "no-acknowledgment"
        return ColorOutcome(
            self.occurrence,
            self.kind,
            status,
            reason,
            None if self.requested is None else len(self.requested),
            len(self.permitted),
            len(self.submitted),
            len(acked),
            None if first is None else (first - self.started) * 1000,
            None if complete is None else (complete - self.started) * 1000,
            self.late,
            self.withdrawals,
            len(self.eligibility_withdrawn),
            (now - self.started) * 1000,
        )
