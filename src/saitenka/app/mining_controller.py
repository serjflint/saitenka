"""Bounded owner of the effective mining target, deck state, and transactions."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka.app import mined_seed, miner
from saitenka.app.anki import AnkiError
from saitenka.app.capabilities import CapabilityProbe
from saitenka.app.mined_set import MinedSet
from saitenka.runtime import EffectFinished, EffectOutcome, Owner

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from PIL.Image import Image as PILImage

    from saitenka.app.anki import Anki, MineConfig
    from saitenka.app.config import MiningOptions
    from saitenka.app.lifecycle_timers import LifecycleTimers
    from saitenka.app.mined_store import MinedCardStore
    from saitenka.app.tokenize import Token
    from saitenka.runtime.jobs import JobSubmitter


@dataclass(frozen=True, slots=True)
class MiningIdentity:
    profile: str
    generation: int


@dataclass(frozen=True, slots=True)
class MiningSpec:
    """Desired local mining policy, before an Anki client is prepared."""

    identity: MiningIdentity
    config: dict | None
    reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.config is not None

    @property
    def target_key(self) -> tuple[object, ...] | None:
        if self.config is None:
            return None
        return (
            self.identity.profile,
            self.identity.generation,
            self.config.get("deck"),
            self.config.get("model") or self.config.get("preset"),
        )

    @classmethod
    def disabled(cls, identity: MiningIdentity, reason: str | None = None) -> MiningSpec:
        return cls(identity, None, reason)


@dataclass(frozen=True, slots=True)
class MiningTarget:
    """Prepared target installed only when it matches the desired spec generation."""

    identity: MiningIdentity
    anki: Anki
    config: MineConfig

    @property
    def key(self) -> tuple[object, ...]:
        return (
            self.identity.profile,
            self.identity.generation,
            self.config.deck,
            self.config.model,
        )


class SeedStatus(StrEnum):
    EMPTY = "empty"
    PENDING = "pending"
    READY = "ready"
    DEGRADED = "degraded"


@dataclass(frozen=True, slots=True)
class MiningIndexSnapshot:
    target_key: tuple[object, ...] | None
    revision: int
    generation: int
    seed_status: SeedStatus
    values: frozenset[str]

    def __contains__(self, expression: object) -> bool:
        return expression in self.values

    def __iter__(self) -> Iterator[str]:
        return iter(self.values)

    def __len__(self) -> int:
        return len(self.values)

    def snapshot(self) -> frozenset[str]:
        return self.values


@dataclass(slots=True)
class MiningIndexState:
    target_key: tuple[object, ...] | None
    revision: int
    seed_status: SeedStatus
    values: MinedSet


@dataclass(frozen=True, slots=True)
class MiningPreviewAccess:
    """Immutable target facts plus named retrieval acts for presentation."""

    deck: str | None
    model: str | None
    fields: tuple[tuple[str, str], ...]
    note_info: Callable[[int], dict | None]
    fetch_image: Callable[[str], PILImage | None]
    fetch_media: Callable[[str], Path | None]


@dataclass(frozen=True, slots=True)
class ForceDuplicate:
    token: Token


@dataclass(frozen=True, slots=True)
class MiningLifecycle:
    seed_submit: JobSubmitter | None
    capability_submit: JobSubmitter | None
    schedule_retry: Callable[[float, Callable[[], None]], bool]
    cancel_retry: Callable[[], object]
    stopped: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class MiningSessionAssembly:
    ipc: object
    capability_submit: JobSubmitter | None
    timers: LifecycleTimers
    stopped: Callable[[], bool]
    settings: MiningOptions
    encounter: Callable[[], miner.MiningEncounter]
    apply: Callable[[], miner.MiningApply]


class MiningController:
    """One writer for mining environment, deck-derived state, and admission policy."""

    def __init__(
        self,
        spec: MiningSpec,
        lifecycle: MiningLifecycle,
        *,
        max_bulk: int,
        anki_ok_ttl: float,
        anki_ping_timeout: float,
        encounter: Callable[[], miner.MiningEncounter],
        apply: Callable[[], miner.MiningApply],
    ) -> None:
        self._spec = spec
        self._target: MiningTarget | None = None
        self._lifecycle = lifecycle
        self._max_bulk = max_bulk
        self._anki_ok_ttl = anki_ok_ttl
        self._anki_ping_timeout = anki_ping_timeout
        self._encounter = encounter
        self._apply = apply
        self._seed = mined_seed.MinedSeedLane()
        self._probe: CapabilityProbe | None = None
        self._scratch = Path(tempfile.mkdtemp(prefix="saitenka-mine-"))
        self._store: MinedCardStore | None = None
        self._closed = False
        self._index = MiningIndexState(spec.target_key, 0, SeedStatus.EMPTY, MinedSet())

    @classmethod
    def for_session(
        cls,
        identity: MiningIdentity,
        anki: Anki | None,
        config: MineConfig | None,
        assembly: MiningSessionAssembly,
    ) -> MiningController:
        """Assemble mining's lanes and lifecycle without exposing their bookkeeping to the session."""
        from saitenka.app.lifecycle_timers import LifecycleTimerKind

        spec = (
            MiningSpec(identity, {"deck": config.deck, "model": config.model})
            if anki is not None and config is not None
            else MiningSpec.disabled(identity)
        )
        controller = cls(
            spec,
            MiningLifecycle(
                seed_submit=mined_seed.configure_runtime_job(assembly.ipc),
                capability_submit=assembly.capability_submit,
                schedule_retry=lambda delay, due: assembly.timers.schedule(
                    LifecycleTimerKind.MINED_SEED_RETRY, delay, due
                ),
                cancel_retry=lambda: assembly.timers.cancel(LifecycleTimerKind.MINED_SEED_RETRY),
                stopped=assembly.stopped,
            ),
            max_bulk=assembly.settings.max_bulk,
            anki_ok_ttl=assembly.settings.anki_ok_ttl,
            anki_ping_timeout=assembly.settings.anki_ping_timeout,
            encounter=assembly.encounter,
            apply=assembly.apply,
        )
        if anki is not None and config is not None:
            controller.publish_prepared_target(MiningTarget(identity, anki, config))
        return controller

    @property
    def desired_spec(self) -> MiningSpec:
        return self._spec

    @property
    def active_target(self) -> MiningTarget | None:
        target = self._target
        return target if target is not None and target.identity == self._spec.identity else None

    @property
    def configured(self) -> bool:
        return self.active_target is not None

    @property
    def target_available(self) -> bool:
        target = self.active_target
        return bool(target is not None and (self._probe is None or self._probe.value is not False))

    @property
    def seed_lane(self) -> mined_seed.MinedSeedLane:
        """Read-only compatibility observation for tests and telemetry."""
        return self._seed

    def index_snapshot(self) -> MiningIndexSnapshot:
        return MiningIndexSnapshot(
            self._index.target_key,
            self._index.revision,
            self._index.values.generation,
            self._index.seed_status,
            self._index.values.snapshot(),
        )

    def record_expression(self, expression: str) -> bool:
        """Record one current-target expression through the sole mutation seam."""
        return self._index.values.add(expression)

    @property
    def store_exists(self) -> bool:
        from saitenka.app import mined_store

        return self._store is not None or mined_store.db_path().exists()

    @property
    def store(self) -> MinedCardStore:
        if self._store is None:
            from saitenka.app.mined_store import MinedCardStore

            self._store = MinedCardStore()
        return self._store

    def select_spec(self, spec: MiningSpec) -> None:
        """Make old target membership invisible before returning to command processing."""
        target = self.active_target
        if (
            target is not None
            and target.identity == spec.identity
            and spec.config is not None
            and target.config.deck == spec.config.get("deck")
            and target.config.model == (spec.config.get("model") or spec.config.get("preset"))
        ):
            self._spec = spec
            return
        self._retire_target_lifecycle()
        self._spec = spec
        self._target = None
        self._index.target_key = spec.target_key
        self._index.revision += 1
        self._index.seed_status = SeedStatus.EMPTY
        self._index.values.replace(())

    def publish_prepared_target(self, target: MiningTarget) -> bool:
        """Install the sole active target path, refusing late profile work."""
        config = self._spec.config
        if (
            self._closed
            or target.identity != self._spec.identity
            or config is None
            or target.config.deck != config.get("deck")
            or target.config.model != (config.get("model") or config.get("preset"))
        ):
            return False
        self._retire_target_lifecycle()
        self._target = target
        self._index.target_key = target.key
        self._index.seed_status = SeedStatus.PENDING
        self._start_probe()
        if self._lifecycle.seed_submit is None:
            self._index.seed_status = SeedStatus.DEGRADED
        return True

    def _start_probe(self) -> None:
        target = self.active_target
        if target is None:
            return
        from saitenka.app.anki import anki_reachable

        self._probe = CapabilityProbe(
            lambda: anki_reachable(timeout=self._anki_ping_timeout),
            name="anki",
            ttl=self._anki_ok_ttl,
            retry=min(self._anki_ok_ttl, 1.0),
            timeout=max(self._anki_ping_timeout * 2, 0.1),
            max_retry=max(self._anki_ok_ttl, 8.0),
            submit=self._lifecycle.capability_submit,
        )
        self._probe.request(force=True)

    def refresh_capability(self) -> None:
        if self._probe is None:
            return
        if self._probe.value:
            self.request_seed()
        self._probe.request()

    def request_seed(self) -> None:
        target = self.active_target
        submit = self._lifecycle.seed_submit
        if target is None or submit is None or not self._seed.idle:
            return
        self._seed.inflight = True
        identity = (target.identity, self._seed.generation, self._index.revision)
        submit(
            owner=Owner.SESSION,
            identity=identity,
            lane="mined-seed",
            request=mined_seed.MinedSeedRequest(target.anki, target.config),
            on_finished=self._finish_seed,
        )

    def _finish_seed(self, completion: EffectFinished) -> None:
        target = self.active_target
        expected = (
            None
            if target is None
            else (target.identity, self._seed.generation, self._index.revision)
        )
        if completion.identity != expected or self._lifecycle.stopped():
            return
        self._seed.inflight = False
        values = completion.result if completion.outcome is EffectOutcome.SUCCEEDED else None
        if not isinstance(values, set):
            self._seed.failures += 1
            self._index.seed_status = SeedStatus.DEGRADED
            self._arm_seed_retry(self._seed.backoff_delay())
            return
        self._seed.done = True
        self._seed.failures = 0
        self._index.seed_status = SeedStatus.READY
        self._index.values.update(values)

    def _arm_seed_retry(self, delay: float) -> None:
        def due() -> None:
            self._seed.retry_pending = False
            self.request_seed()

        self._seed.retry_pending = self._lifecycle.schedule_retry(delay, due)

    def mine_target(self) -> int | None:
        if not self.configured:
            return None
        return miner.mine_target(self._encounter().cue)

    def mine_index(self, index: int, *, animated: bool | None = None) -> None:
        context = self._operation()
        if context is None or index < 0 or index >= len(context.encounter.cue.tokens):
            return
        token = context.encounter.cue.tokens[index]
        cards = (
            context.encounter.dict_set.cards_for(token, extra_terms=context.encounter.hovered_terms)
            if (
                context.encounter.dict_set
                and index == context.encounter.cue.hover
                and context.encounter.hovered_terms
            )
            else []
        )
        miner.mine_token(context, token, card=cards[0] if cards else None, animated=animated)

    def mine_token(self, token: Token, *, card=None) -> None:
        context = self._operation()
        if context is not None:
            miner.mine_token(context, token, card=card)

    def force_duplicate(self, request: ForceDuplicate) -> None:
        context = self._operation()
        if context is not None:
            miner.mine_token(context, request.token, force=True)

    def bulk_mine(self) -> None:
        context = self._operation()
        if context is not None:
            miner.bulk_mine(context)

    def _operation(self) -> miner.MiningTransaction | None:
        if self._closed:
            return None
        target = self.active_target
        if target is None:
            return None
        encounter = self._encounter()
        cue = miner.MineCue(
            encounter.cue.tokens,
            encounter.cue.styles,
            encounter.cue.hover,
            encounter.cue.tokenizer,
            self._max_bulk,
        )
        encounter = miner.MiningEncounter(
            cue,
            encounter.dict_set,
            encounter.ipc,
            encounter.media_path,
            encounter.playhead,
            encounter.sentence_html,
            encounter.hovered_terms,
        )
        external = self._apply()

        def mark(expression: str) -> None:
            self.record_expression(expression)
            external.mark_mined(expression)

        apply = miner.MiningApply(
            external.toast,
            external.reset_capture,
            external.captured_image,
            external.captured_audio,
            mark,
            external.mined_here,
            external.remember_duplicate,
            external.preview_existing,
            external.preview_mined,
            external.record_mined,
        )
        return miner.MiningTransaction(
            target.anki, target.config, self.store, self._scratch, encounter, apply
        )

    def preview_access(self) -> MiningPreviewAccess:
        target = self.active_target
        if target is None:
            return MiningPreviewAccess(
                None, None, (), lambda _id: None, lambda _n: None, lambda _n: None
            )

        def note_info(note_id: int) -> dict | None:
            try:
                rows = target.anki.notes_info([note_id])
            except (OSError, AnkiError, json.JSONDecodeError):
                return None
            return rows[0] if rows else None

        from saitenka.app import miner_ui

        return MiningPreviewAccess(
            target.config.deck,
            target.config.model,
            tuple(target.config.fields.items()),
            note_info,
            lambda name: miner_ui.media_image(target.anki, name),
            lambda name: miner_ui.media_tempfile(target.anki, name, self._scratch),
        )

    def _retire_target_lifecycle(self) -> None:
        self._lifecycle.cancel_retry()
        self._seed.restart()
        if self._probe is not None:
            self._probe.close()
            self._probe = None

    def close_capability(self) -> None:
        """Retire mining's probe and seed policy in the capability close phase."""
        self._retire_target_lifecycle()

    def invalidate(self) -> None:
        """Refuse in-flight seed publication before the fallible close ledger starts."""
        self._seed.invalidate()

    def close_store(self) -> None:
        if self._store is not None:
            self._store.close()

    def retire_artifacts(self, delegate: Callable[[str], bool] | None = None) -> None:
        try:
            if delegate is not None and delegate(str(self._scratch)):
                return
            shutil.rmtree(self._scratch, ignore_errors=True)
        finally:
            self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._retire_target_lifecycle()
        self.close_store()
        self.retire_artifacts()
