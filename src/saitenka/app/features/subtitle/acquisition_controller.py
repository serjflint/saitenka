"""Episode-aware subtitle acquisition and completion refusal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from saitenka.app import subtitle_modes
from saitenka.runtime import EffectFinished, Owner

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable

    from saitenka.app.session.context import EpisodeSlot
    from saitenka.app.subtitle_intents import AcquisitionSource
    from saitenka.app.subtitle_modes import (
        PropertyGet,
        ProviderFetch,
        ProviderFetchFactory,
        TrackPorts,
    )
    from saitenka.app.toast_controller import NotificationSink
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.jobs import JobSubmitter


class SubtitleAcquisitionController:
    """Own provider retries, fetch identity, and episode-safe result application."""

    def __init__(
        self,
        *,
        ipc: MpvIPC,
        episodes: EpisodeSlot,
        stop: threading.Event,
        get: PropertyGet,
        notifications: NotificationSink,
        track_ports: Callable[[], TrackPorts],
        submitter: JobSubmitter | None,
    ) -> None:
        self._ipc = ipc
        self._episodes = episodes
        self._stop = stop
        self._get = get
        self._notifications = notifications
        self._track_ports = track_ports
        self._submitter = submitter
        self._sequence = 0
        self._force_select_revision = 0

    @property
    def retry_in_flight(self) -> bool:
        return self._episodes.current.subtitle.retry_active

    def configure_retry(self, factory: ProviderFetchFactory | None) -> None:
        subtitle_modes.configure_retry(self._episodes.current.subtitle, factory)

    def start(
        self,
        fetch: ProviderFetch,
        *,
        name: str = "sub-provider",
        select_if_unchanged: bool = False,
        replace: bool = False,
        force_select: bool = False,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        subtitle_modes.start_fetch(
            self.submit,
            self._get,
            fetch,
            name=name,
            select_if_unchanged=select_if_unchanged,
            replace=replace,
            force_select=force_select,
            on_done=on_done,
        )

    def begin(self, media_path: str, source: AcquisitionSource) -> None:
        subtitle_modes.begin_acquisition(
            self.submit,
            self._get,
            self._notifications.show,
            self._episodes.subtitle_source,
            self._ipc,
            media_path,
            source,
        )

    def fetch_background(self, fetch: ProviderFetch) -> None:
        self.start(fetch, select_if_unchanged=True)

    def submit(
        self,
        request: subtitle_modes.SubtitleFetchRequest,
        *,
        name: str,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        episode = self._episodes.current
        self._sequence += 1
        identity = (self._sequence, name)
        force_select_revision = None
        if request.force_select:
            self._force_select_revision += 1
            force_select_revision = self._force_select_revision

        def finish(completion: EffectFinished) -> None:
            if (
                episode is not self._episodes.current
                or self._stop.is_set()
                or (
                    force_select_revision is not None
                    and force_select_revision != self._force_select_revision
                )
            ):
                return
            try:
                subtitle_modes.apply_fetch_result(
                    self._track_ports(), subtitle_modes.finish_fetch(request, completion)
                )
            finally:
                if on_done is not None:
                    on_done()

        if self._submitter is None:
            subtitle_modes.apply_fetch_result(
                self._track_ports(), subtitle_modes.unavailable_fetch(request)
            )
            if on_done is not None:
                on_done()
            return
        self._submitter(
            owner=Owner.SUBTITLE,
            identity=identity,
            lane="subtitle-fetch",
            request=request,
            on_finished=finish,
        )

    def retire_episode(self) -> None:
        """Refuse any explicit selection that completes after this episode retires."""
        self._force_select_revision += 1
