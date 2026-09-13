"""Per-view submission quality, independent of composition totals and display presentation."""

from __future__ import annotations

import time
from uuid import uuid4

from saitenka import otel_metrics


class ViewQuality:
    def __init__(self) -> None:
        self.view_id = uuid4().hex
        self.generation = 0
        self._panel: object = None
        self._viewport: object = None
        self._state = "closed"
        self._acknowledged = "unknown"
        self._since = time.monotonic()
        self._pending: otel_metrics.DeferredSpan | None = None

    def prepare(self, panel, viewport) -> None:
        if panel != self._panel:
            self.close("replaced")
            self.view_id = uuid4().hex
            self._panel = panel
        if viewport != self._viewport:
            if self._pending is not None:
                self._pending.finish(outcome="superseded", reason="viewport-changed")
                self._pending = None
            self._acknowledged = "unknown"
            self.generation += 1
            self._viewport = viewport

    def submit(self, panel, viewport, *, kind: str, state: str, reason: str, job_id):
        now = time.monotonic()
        self.prepare(panel, viewport)
        attrs = {
            "view_id": self.view_id,
            "viewport_revision": str(self.generation),
            "kind": kind,
            "quality": state,
            "reason": reason,
            "job_id": str(job_id),
            "endpoint": "submission",
        }
        if state != self._state:
            with otel_metrics.traced("tooltip_quality_transition", **attrs) as span:
                span.set("previous", self._state)
                span.set("previous_ms", round((now - self._since) * 1000, 3))
            self._state, self._since = state, now
        operation = otel_metrics.DeferredSpan("tooltip_quality_submission", **attrs)
        if self._pending is not None:
            self._pending.finish(outcome="superseded")
        self._pending = operation
        return operation

    def settle(self, operation, *, accepted: bool, state: str) -> None:
        operation.finish(
            outcome="acknowledged" if accepted else "failed", endpoint="mpv-acknowledgment"
        )
        if operation is not self._pending:
            return
        self._pending = None
        if accepted:
            self._acknowledged = state
            with otel_metrics.traced(
                "tooltip_quality_acknowledged",
                view_id=self.view_id,
                viewport_revision=str(self.generation),
                quality=state,
                endpoint="mpv-acknowledgment",
            ):
                pass

    def close(self, reason: str = "closed") -> None:
        if self._pending is not None:
            self._pending.finish(outcome="cancelled", reason=reason)
            self._pending = None
        if self._state == "closed":
            return
        with otel_metrics.traced(
            "tooltip_quality_end", view_id=self.view_id, reason=reason
        ) as span:
            span.set("quality", self._state)
            span.set("endpoint", "submission")
            span.set("acknowledged_quality", self._acknowledged)
            span.set("duration_ms", round((time.monotonic() - self._since) * 1000, 3))
            span.set("upgrade_abandoned", self._acknowledged != "crisp")
        self._state = "closed"
        self._acknowledged = "unknown"
        self._panel = None
