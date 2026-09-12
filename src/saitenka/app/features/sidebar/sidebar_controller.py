"""Owner of sidebar state, paint geometry, and surface policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app import backlog
from saitenka.app.feature_bindings import SIDEBAR_STATEFUL_BINDING
from saitenka.app.features.sidebar import sidebar
from saitenka.app.interaction.surfaces import ClickTarget, HoverSuppression, SurfaceSpec, WheelStep
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.model import claims_pointer
from saitenka.runtime import events

if TYPE_CHECKING:
    from saitenka.app.features.analysis.analysis_controller import AnalysisController
    from saitenka.app.features.help.help_controller import ScreenState
    from saitenka.app.features.history.history_owner import HistoryOwner
    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.profiles.profile_session import ProfileSession
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.session.playback_observation import PlaybackObservationController
    from saitenka.app.subtitle_adapter import SubtitleTrackCoordinator
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.interaction_slice import SidebarStore
    from saitenka.runtime.sidebar import SidebarState


@dataclass(frozen=True, slots=True)
class SidebarViewOwners:
    tracks: SubtitleTrackCoordinator
    playback: PlaybackObservationController
    screen: ScreenState
    surfaces: LifecycleSurfaces
    history: HistoryOwner
    mining: MiningController
    profile: ProfileSession
    analysis: AnalysisController
    timers: LifecycleTimers


_UNREAD = object()


class SidebarController:
    def __init__(self, ipc: MpvIPC) -> None:
        self.store: SidebarStore = SIDEBAR_STATEFUL_BINDING.store(ipc)
        self.panel = sidebar.SidebarPanel()
        self._view_owners: SidebarViewOwners | None = None
        #: The episode's navigation state the cached path was read for — the object itself, not its
        #: id, which the allocator hands to the next episode's; a sentinel so the first read happens.
        self._video_path_key: object = _UNREAD
        self._video_path_value: str | None = None

    def bind_view(self, owners: SidebarViewOwners) -> None:
        if self._view_owners is not None:
            raise RuntimeError("sidebar view owners already bound")
        self._view_owners = owners

    def view(self) -> sidebar.SidebarView:
        owners = self._view_owners
        if owners is None:
            raise RuntimeError("sidebar view owners are not bound")
        navigation = owners.tracks.navigation.current
        playback = owners.playback
        return sidebar.SidebarView(
            store=self.store,
            panel=self.panel,
            active=sidebar._active_index(
                navigation.sub_index,
                playback.cue.text,
                # Observed facts, not round trips: this runs every turn ahead of the cue settle,
                # and a read blocks for as long as mpv is busy — 250 ms across a seek.
                sub_start=playback.value("sub-start"),
                time_pos=playback.value("time-pos"),
                preferred=navigation.nav_idx,
            ),
            index=navigation.sub_index,
            language=owners.tracks.current().language,
            osd=owners.screen.osd,
            chrome_scale=owners.screen.chrome_scale(),
            surfaces=owners.surfaces,
            video=self._video_path(navigation, playback),
            backlog=owners.history.ensure_backlog,
            mined=lambda: owners.mining.store,
            mined_exists=owners.mining.store_exists,
            backlog_exists=owners.history.backlog is not None or backlog.db_path().exists(),
            scorer=owners.profile.scorer,
            tokenizer=owners.profile.profile.tokenizer,
            analysis=owners.analysis.result,
            can_mine=owners.mining.configured,
        )

    def _video_path(self, episode: object, playback: PlaybackObservationController) -> str | None:
        """The media path, read from mpv once per episode rather than on every turn.

        `view()` is built every turn ahead of the cue settle, and `path` is not an observed
        property, so reading it live put a blocking round trip on the color path — 250 ms while
        mpv is mid-seek. Keyed on the navigation state, which the episode rebind replaces whether
        or not a sub-index ever loads for it.
        """
        if self._video_path_key is not episode:
            self._video_path_key = episode
            self._video_path_value = playback.text("path")
        return self._video_path_value

    def show(self) -> None:
        sidebar.show(self.view())

    def hide(self) -> None:
        sidebar.hide(self.view())

    def follow(self) -> None:
        sidebar.follow(self.view())

    def index_changed(self) -> None:
        sidebar.index_changed(self.view())

    def mark_active_mined(self) -> None:
        sidebar.mine_active(self.view())

    def hold_scroll(self, seconds: float) -> bool:
        owners = self._view_owners
        if owners is None:
            return False

        def released() -> None:
            self.store.dispatch(events.SidebarHoldReleased())
            self.follow()

        return owners.timers.schedule(
            LifecycleTimerKind.SIDEBAR_MANUAL_HOLD,
            seconds,
            released,
        )

    @property
    def state(self) -> SidebarState:
        return self.store.current

    def surface_state(self) -> SidebarState:
        return self.state

    def suppress_hover(self, suppression: HoverSuppression) -> bool:
        state = self.state
        if not state.open:
            return False
        if not claims_pointer(self.panel.rect, suppression.pointer, open_=state.open):
            return False
        suppression.hide_annotation()
        suppression.release_hover()
        return True

    @staticmethod
    def scroll(wheel: WheelStep, steps: int) -> bool:
        return sidebar.wheel(
            wheel.sidebar,
            steps,
            wheel.pointer,
            hold=wheel.hold_sidebar,
        )

    @staticmethod
    def on_click(target: ClickTarget, x: float, y: float) -> bool:
        return sidebar.click(target.sidebar, target.sidebar_acts, x, y)

    def surface_binding(self) -> SurfaceSpec:
        return SurfaceSpec(
            "sidebar",
            state_of=self.surface_state,
            suppress_hover=self.suppress_hover,
            scroll=self.scroll,
            on_click=self.on_click,
        )
