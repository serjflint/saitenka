"""Session-scoped operations that coordinate a small, named set of owners."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from saitenka import otel_metrics
from saitenka.app import session_stats
from saitenka.app.lifecycle_timers import LifecycleTimerKind

if TYPE_CHECKING:
    import threading

    from saitenka.app.capabilities import CapabilityProbe
    from saitenka.app.features.analysis.analysis_controller import AnalysisController
    from saitenka.app.features.annotation.annotation_controller import CueAnnotationController
    from saitenka.app.features.help.help_controller import HelpController, ScreenState
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.picker.picker_controller import PickerController
    from saitenka.app.features.profiles.profile_session import ProfileSession
    from saitenka.app.features.sidebar.sidebar_controller import SidebarController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.subtitle_adapter import SubtitleTrackCoordinator
    from saitenka.app.subtitle_presentation import SubtitlePresentation
    from saitenka.app.toast_controller import ToastController
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.mpvio.osd import Overlay
    from saitenka.runtime.subtitle_slice import SubtitleTrackStore

log = logging.getLogger(__name__)

_DISPLAY_PROBE_PROPS = (
    "osd-width",
    "osd-height",
    "osd-par",
    "dwidth",
    "dheight",
    "width",
    "height",
    "window-scale",
    "current-window-scale",
    "display-hidpi-scale",
    "display-fps",
    "display-names",
    "fullscreen",
    "window-maximized",
    "window-minimized",
    "focused",
    "video-out-params",
)


class PlaybackAccess(Protocol):
    def __call__(self) -> PlaybackObservationController: ...


@dataclass(slots=True)
class SessionPresentation:
    """Own render-space observation and the paused-overlay repaint policy."""

    playback: PlaybackAccess
    screen: ScreenState
    subtitles: SubtitlePresentation
    tooltip: TooltipController
    help_controller: HelpController
    analysis: AnalysisController
    sidebar: SidebarController
    surfaces: LifecycleSurfaces
    timers: LifecycleTimers
    overlay: Overlay
    _nudge_pending: bool = field(default=False, init=False)

    def refresh_osd(self) -> bool:
        playback = self.playback()
        dimensions = playback.value("osd-dimensions") or {}
        width = int(dimensions.get("w") or self.screen.osd[0])
        height = int(dimensions.get("h") or self.screen.osd[1])
        if (width, height) == self.screen.osd or width <= 0 or height <= 0:
            return False
        self.screen.osd = (width, height)
        if self.subtitles.native is None:
            self.subtitles.pipeline.invalidate()
        self.probe_display_sources("osd-change", dimensions)
        return True

    def probe_display_sources(self, reason: str, osd: dict) -> None:
        playback = self.playback()
        probe = {name: playback.query(name) for name in _DISPLAY_PROBE_PROPS}
        raw_video = probe.get("video-out-params")
        video = raw_video if isinstance(raw_video, dict) else {}
        attrs = {
            "reason": reason,
            "tip_scale": f"{self.tooltip.scale().display:.4f}",
            "osd_w": str(osd.get("w")),
            "osd_h": str(osd.get("h")),
            "osd_mt": str(osd.get("mt")),
            "osd_mb": str(osd.get("mb")),
            "hidpi_scale": str(probe.get("display-hidpi-scale")),
            "window_scale": str(probe.get("current-window-scale") or probe.get("window-scale")),
            "dwidth": str(probe.get("dwidth")),
            "dheight": str(probe.get("dheight")),
            "vop_dh": str(video.get("dh")),
            "fullscreen": str(probe.get("fullscreen")),
        }
        with otel_metrics.traced("osd_probe", **attrs):
            pass
        log.info(
            "display sources (%s): tip_scale=%s osd=%r probe=%r",
            reason,
            attrs["tip_scale"],
            osd,
            probe,
        )

    def redraw_after_resize(self) -> None:
        if not self.refresh_osd():
            return
        if self.playback().cue.text.strip():
            self.subtitles.draw()
        self.help_controller.redraw()
        self.analysis.redraw()
        self.sidebar.follow()

    def schedule_paused_nudge(self, operations_before: int) -> None:
        if self.overlay.ops == operations_before or not self.playback().value("pause"):
            return
        self._nudge_pending = self.timers.schedule(
            LifecycleTimerKind.PAUSED_REPAINT,
            0.0,
            self._flush_paused_nudge,
        )
        if not self._nudge_pending:
            self._flush_paused_nudge()
        if otel_metrics.osd_paused_draw is not None:
            otel_metrics.osd_paused_draw.add(1)

    def _flush_paused_nudge(self) -> None:
        self._nudge_pending = False
        self.surfaces.repaint()
        if otel_metrics.osd_paused_nudge is not None:
            otel_metrics.osd_paused_nudge.add(1)


@dataclass(slots=True)
class SessionRecurrence:
    """Own recurring capability and history deadlines."""

    tts: CapabilityProbe | None
    mining: MiningController
    timers: LifecycleTimers
    history: HistoryOwner
    playback: PlaybackObservationController
    tracks: SubtitleTrackStore

    def arm_capabilities(self, seconds: float = 0.5) -> None:
        self.timers.schedule(
            LifecycleTimerKind.CAPABILITY_REFRESH,
            seconds,
            lambda: self._refresh_capabilities(seconds),
        )

    def _refresh_capabilities(self, seconds: float) -> None:
        self.refresh_capabilities()
        self.arm_capabilities(seconds)

    def refresh_capabilities(self) -> None:
        if self.tts is not None:
            self.tts.request()
        self.mining.refresh_capability()

    def arm_history(self, seconds: float) -> None:
        self.timers.schedule(
            LifecycleTimerKind.SESSION_PERSIST,
            seconds,
            lambda: self._persist_history(seconds),
        )

    def _persist_history(self, seconds: float) -> None:
        session_stats.accrue(
            self.history.recorder,
            paused=bool(self.playback.value("pause")),
            language=self.tracks.current.language,
        )
        self.arm_history(seconds)


@dataclass(frozen=True, slots=True)
class SessionDiagnostics:
    ipc: MpvIPC
    playback: PlaybackObservationController
    annotation: CueAnnotationController
    profile: ProfileSession
    tooltip: TooltipController
    subtitles: SubtitlePresentation

    def gauges(self) -> dict[str, float]:
        panel_count, panel_bytes = self.tooltip.cache_totals()
        dictionaries = self.profile.profile.dict_set
        values = {
            "panel_cache.size": float(panel_count),
            "panel_cache.bytes": float(panel_bytes),
            "dict_cache.size": float(
                dictionaries.decoded_entry_count() if dictionaries is not None else 0
            ),
        }
        if self.subtitles.native is None:
            return values
        stats = self.subtitles.native.worker.stats
        values.update(
            {
                "subtitle_geometry.submitted": float(stats.submitted),
                "subtitle_geometry.superseded": float(stats.superseded),
                "subtitle_geometry.completed": float(stats.completed),
                "subtitle_geometry.cache_hits": float(stats.cache_hits),
                "subtitle_geometry.failures": float(stats.failures),
                "subtitle_geometry.ready_before_presented": float(stats.ready_before_presented),
                "subtitle_geometry.presented": float(stats.presented),
                "subtitle_geometry.max_submit_us": float(stats.max_submit_us),
                "subtitle_geometry.prefetched": float(stats.prefetched),
                "subtitle_geometry.prefetch_dropped": float(stats.prefetch_dropped),
                "subtitle_geometry.result_cache_entries": float(stats.result_cache_entries),
                "subtitle_geometry.prefetch_cache_entries": float(stats.prefetch_cache_entries),
            }
        )
        return values

    def check_startup_health(self) -> None:
        bytes_read = self.ipc._bytes_read
        osd_ready = self.playback.value("osd-dimensions") not in (None, {})
        if bytes_read == 0 or not osd_ready:
            log.warning(
                "IPC looks dead 8s after start (bytes from mpv=%d, osd-dimensions=%s)",
                bytes_read,
                "ok" if osd_ready else "None",
            )
        elif not self.playback.cue.text:
            log.debug("IPC alive 8s after start but no subtitle text has arrived")


@dataclass(frozen=True, slots=True)
class PickerCommandEndpoint:
    picker: PickerController
    playback: PlaybackObservationController
    tooltip: TooltipController
    tracks: SubtitleTrackCoordinator
    stop: threading.Event
    notifications: ToastController

    def run(self) -> None:
        self.picker.open(
            self.playback.query("path"),
            retire_hover=self.tooltip.retire_hover,
            navigation=self.tracks.navigation,
            stop=self.stop,
            toast=self.notifications.show,
        )
