"""Session-lived profile dependencies and their progressive installation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.profiles import dependencies as profile_dependencies
from saitenka.app.lifecycle_timers import LifecycleTimerKind
from saitenka.app.overlay_ids import OverlayId
from saitenka.app.perf import gil_disabled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from concurrent.futures import Future

    from saitenka.app.features.mining.mining_controller import MiningController
    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.lifecycle_surfaces import LifecycleSurfaces
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.profiles import Profile
    from saitenka.app.scoring import Coloring


@dataclass(frozen=True, slots=True)
class ProfileDependencyPorts:
    """Owner-thread acts required when a dependency generation changes."""

    enable_async_annotation: Callable[[], None]
    dependencies_changed: Callable[[], None]
    start_prefetch: Callable[[], int]
    warm_episode: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ProfileSessionAssembly:
    profile: ProfileController
    mining: MiningController
    timers: LifecycleTimers
    surfaces: LifecycleSurfaces
    ports: ProfileDependencyPorts
    worker_count: Callable[[], int]
    log_runtime: Callable[[str, int], None]


class ProfileSession:
    """Own the active profile's replaceable collaborators and loading lifecycle."""

    def __init__(
        self,
        assembly: ProfileSessionAssembly,
        *,
        identity,
        scorer: Coloring | None,
    ) -> None:
        self.profile = assembly.profile
        self._mining = assembly.mining
        self._timers = assembly.timers
        self._surfaces = assembly.surfaces
        self._ports = assembly.ports
        self._worker_count = assembly.worker_count
        self._log_runtime = assembly.log_runtime
        self._dependencies = profile_dependencies.ProfileDependencies(identity, self._apply())
        self._scorer = scorer
        self._loading = False
        self._loading_frame = 0
        self._runtime_announced = False

    @property
    def scorer(self) -> Coloring | None:
        return self._scorer

    @property
    def loading(self) -> bool:
        return self._loading

    @property
    def ready(self) -> bool:
        return self._dependencies.ready

    @property
    def identity(self):
        return self._dependencies.identity

    def select(self, profile: Profile) -> None:
        """Begin a dependency generation for a committed profile switch."""
        self._dependencies.select(profile)

    def configure(
        self,
        profiles: Sequence[Profile],
        *,
        dependency_builder_for,
        mining_spec_for,
        dict_scoper=None,
        base_slang: str = "ja,jpn,jp",
        environment_select: Callable[[Profile], None] | None = None,
    ) -> None:
        from saitenka.app.features.profiles.profile_controller import ProfileEnvironment

        self._dependencies.configure(dependency_builder_for, mining_spec_for)

        def select_environment(profile: Profile) -> None:
            self._dependencies.select(profile)
            if environment_select is not None:
                environment_select(profile)

        self.profile.configure_cycle(
            profiles,
            dict_scoper,
            base_slang=base_slang,
            environment=ProfileEnvironment(select_environment),
        )

    def load(self, cfg: dict, build=None, *, prebuilt: Future | None = None) -> None:
        self._dependencies.load(cfg, build, prebuilt=prebuilt)

    def drain(self) -> None:
        self._dependencies.drain()

    def accept(self, deps: profile_dependencies.DependencyBundle) -> None:
        self._dependencies.accept(deps)

    def publish(self, deps: profile_dependencies.DependencyBundle) -> None:
        """Hand a worker result to the owner-thread queue."""
        self._dependencies.publish(deps)

    def begin_loading(self) -> None:
        """Start the progressive-loading presentation."""
        self._loading = True
        self._schedule_loading_frame(delay_s=0.0)

    def announce_if_ready(self) -> None:
        if self.profile.dict_set is not None:
            self._announce_runtime()

    def _load_ports(self) -> profile_dependencies.DepsLoad:
        return profile_dependencies.DepsLoad(
            begin_loading=self.begin_loading,
            enable_async_annotation=self._ports.enable_async_annotation,
            publish=self.publish,
            announce=self._announce_ready,
        )

    def _announce_ready(self) -> bool:
        return self._timers.schedule(
            LifecycleTimerKind.DEPS_READY,
            0.0,
            self.drain,
        )

    def _apply(self) -> profile_dependencies.ProfileDependencyApply:
        return profile_dependencies.ProfileDependencyApply(
            load_ports=self._load_ports,
            selected_profile=lambda: self.profile.profile,
            select_mining=self._mining.select_mining_spec,
            retire_current=self._retire_current,
            stop_loading=self._stop_loading,
            install=self._install,
            arrived=self._arrived,
        )

    def _retire_current(self) -> None:
        self._scorer = None
        self._ports.dependencies_changed()

    def _stop_loading(self) -> None:
        self._loading = False
        self._timers.cancel(LifecycleTimerKind.LOADING_FRAME)
        self._surfaces.remove(OverlayId.LOADING)
        self._mining.close_capability()

    def _install(self, deps: profile_dependencies.DependencyBundle) -> None:
        self._scorer = deps.scorer
        self.profile.replace_dictionary_set(deps.dictionaries)
        if deps.mining is None:
            self._mining.clear_mining_target(deps.identity)
        else:
            self._mining.publish_mining_target(deps.mining)

    def _arrived(self) -> None:
        self._ports.dependencies_changed()
        workers = self._ports.start_prefetch()
        self._ports.warm_episode()
        self._announce_runtime(workers)

    def _schedule_loading_frame(self, *, delay_s: float) -> bool:
        return self._timers.schedule(
            LifecycleTimerKind.LOADING_FRAME,
            delay_s,
            self._loading_frame_due,
        )

    def _loading_frame_due(self) -> None:
        if not self._loading or self._dependencies.ready:
            return
        profile_dependencies.draw_loading(self._surfaces, self._loading_frame)
        self._loading_frame += 1
        self._schedule_loading_frame(delay_s=0.08)

    def _announce_runtime(self, worker_count: int | None = None) -> None:
        if self._runtime_announced:
            return
        self._runtime_announced = True
        mode = "free-threaded (GIL off)" if gil_disabled() else "GIL"
        self._log_runtime(mode, self._worker_count() if worker_count is None else worker_count)
