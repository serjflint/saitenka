"""The single writer for backlog storage and the current study-history row."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app import session_stats

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.backlog import BacklogStore
    from saitenka.app.session_stats import SessionRecorder
    from saitenka.runtime.analysis import EpisodeAnalysis


class HistoryOwner:
    def __init__(self, *, enabled: bool = False, summary: bool = False) -> None:
        self._backlog: BacklogStore | None = None
        self._recorder: SessionRecorder | None = None
        self._enabled = enabled
        self._summary = summary

    @property
    def backlog(self) -> BacklogStore | None:
        return self._backlog

    @property
    def recorder(self) -> SessionRecorder | None:
        return self._recorder

    def ensure_backlog(self) -> BacklogStore:
        if self._backlog is None:
            from saitenka.app.backlog import BacklogStore

            self._backlog = BacklogStore()
        return self._backlog

    def replace_backlog(self, store: BacklogStore | None) -> None:
        self._backlog = store

    def replace_recorder(self, recorder: SessionRecorder | None) -> None:
        self._recorder = recorder

    def start(
        self,
        *,
        path: Callable[[], object],
        arm: Callable[[float], object],
    ) -> None:
        self._recorder = session_stats.start(
            current=self._recorder,
            enabled=self._enabled,
            path=path,
            arm=arm,
        )

    def record_lookup(self) -> None:
        if self._recorder is not None:
            self._recorder.record_lookup()

    def record_capture(self) -> None:
        if self._recorder is not None:
            self._recorder.record_capture()

    def record_mined(self, count: int) -> None:
        if self._recorder is not None:
            self._recorder.record_mined(count)

    def finish(self, analysis: EpisodeAnalysis | None) -> str | None:
        recorder, self._recorder = self._recorder, None
        return session_stats.finish(recorder, analysis)

    def report(self, summary: str | None) -> None:
        if not summary or not self._summary:
            return
        from saitenka.app import logsetup

        logsetup.user_facing_logger().info("session: %s", summary)

    def close_backlog(self) -> None:
        if self._backlog is not None:
            self._backlog.close()
