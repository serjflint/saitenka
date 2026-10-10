"""Owner of card-preview state and paint/process state."""

from __future__ import annotations

import logging
import threading
from functools import partial
from typing import TYPE_CHECKING

from saitenka.app.feature_bindings import PREVIEW_STATEFUL_BINDING
from saitenka.app.features.preview.card_preview import PreviewPanel
from saitenka.app.features.preview.preparation import (
    LANE,
    PreviewDestination,
    PreviewRequest,
    run_preparation,
)
from saitenka.app.interaction.surfaces import SurfaceSpec
from saitenka.runtime import EffectError, EffectFinished, EffectId, EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, configure_lane

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from saitenka_tokenize.japanese import Token

    from saitenka.app.features.preview.miner_ui import CardSource, PreparedPreview, PreviewPorts
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.card_preview import CardPreview
    from saitenka.runtime.interaction_slice import PreviewStore


log = logging.getLogger(__name__)


class PreviewController:
    def __init__(self, ipc: MpvIPC) -> None:
        self.store: PreviewStore = PREVIEW_STATEFUL_BINDING.store(ipc)
        self.panel = PreviewPanel()
        self._submit = configure_lane(ipc, LANE, JobLanePolicy(capacity=1), run_preparation)
        self._sequence = 0
        self._latest: PreviewRequest | None = None
        self._pending: PreviewRequest | None = None
        self._inflight: PreviewRequest | None = None
        self._destination_arrived = False
        self._closed = False

    @property
    def state(self) -> CardPreview:
        return self.store.current

    def surface_state(self) -> CardPreview:
        return self.state

    def surface_binding(self) -> SurfaceSpec:
        return SurfaceSpec("preview", state_of=self.surface_state)

    def reset_capture(self) -> None:
        self.cancel()
        self.panel.last_jpg = self.panel.last_audio = None

    def captured_image(self, path: Path) -> None:
        self.panel.last_jpg = path

    def captured_audio(self, path: Path) -> None:
        self.panel.last_audio = path

    def remember_duplicate(self, token: Token | None) -> None:
        self.panel.dup_tok = token

    def present_mined(
        self,
        ports: Callable[[], PreviewPorts],
        source: Callable[[], CardSource],
        toast: Callable[..., object],
        card,
        token,
        video,
        status: str = "mined",
        *,
        enabled: bool,
    ) -> None:
        if not enabled:
            toast(f"mined {card.expression}")
            return
        from saitenka.app.features.preview import miner_ui

        captured = source()
        self._request(
            partial(
                miner_ui.prepare_mined,
                captured,
                card,
                token,
                video,
                status,
                self.panel.last_jpg,
                self.panel.last_audio,
            ),
            ports,
            toast,
            f"mined {card.expression}",
            current=captured.current,
        )

    def present_existing(
        self,
        ports: Callable[[], PreviewPorts],
        source: Callable[[], CardSource],
        toast: Callable[..., object],
        note_id: int,
        card,
        status: str,
        *,
        enabled: bool,
        destination: PreviewDestination | None = None,
    ) -> None:
        if not enabled:
            toast(f"already have {card.expression}")
            return
        from saitenka.app.features.preview import miner_ui

        captured = source()
        self._request(
            partial(miner_ui.prepare_existing, captured, note_id, card, status),
            ports,
            toast,
            f"already have {card.expression}",
            current=captured.current,
            destination=destination,
        )

    def cancel(self) -> None:
        for request in (self._latest, self._inflight):
            if request is not None:
                request.cancelled.set()
        self._latest = self._pending = None
        self._destination_arrived = False

    def close(self) -> None:
        self._closed = True
        self.cancel()

    def retains_cue(self, text: str, start: object, end: object) -> bool:
        request = self._latest
        destination = None if request is None else request.destination
        if destination is None or request is None or not request.current():
            return False
        if (
            isinstance(start, int | float)
            and isinstance(end, int | float)
            and start < destination.end
            and destination.start < end
        ):
            self._destination_arrived = True
            return True
        return not self._destination_arrived and (
            not text.strip() or text == destination.previous_text
        )

    def _request(
        self,
        build: Callable[[], PreparedPreview | None],
        ports: Callable[[], PreviewPorts],
        toast: Callable[..., object],
        fallback: str,
        *,
        current: Callable[[], bool],
        destination: PreviewDestination | None = None,
    ) -> None:
        if self._closed:
            return
        self.cancel()
        self._sequence += 1
        request = PreviewRequest(
            self._sequence, build, ports, toast, fallback, threading.Event(), current, destination
        )
        self._latest = self._pending = request
        self._pump()

    def _pump(self) -> None:
        if self._closed or self._inflight is not None or self._pending is None:
            return
        request, self._pending = self._pending, None
        if not request.current():
            self.cancel()
            return
        self._inflight = request
        if self._submit is None:
            self._run_inline(request)
            return
        accepted = self._submit(
            owner=Owner.INTERACTION,
            identity=request.sequence,
            lane=LANE,
            request=request,
            on_finished=partial(self._finished, request),
        )
        if not accepted and self._inflight is request:
            self._finished(
                request,
                EffectFinished(
                    EffectId(0), Owner.INTERACTION, request.sequence, EffectOutcome.REJECTED
                ),
            )

    def _run_inline(self, request: PreviewRequest) -> None:
        result = None
        outcome = EffectOutcome.SUCCEEDED
        error = None
        try:
            result = run_preparation(request, threading.Event())
        except Exception:
            log.exception("preview preparation failed")
            outcome = EffectOutcome.FAILED
            error = EffectError.INTERNAL
        self._finished(
            request,
            EffectFinished(
                EffectId(0), Owner.INTERACTION, request.sequence, outcome, result, error
            ),
        )

    def _finished(self, request: PreviewRequest, completion: EffectFinished) -> None:
        from saitenka.app.features.preview.miner_ui import PreparedPreview, show_preview

        if self._inflight is not request:
            return
        self._inflight = None
        try:
            if self._closed or self._latest is not request or request.cancelled.is_set():
                return
            if not request.current():
                self.cancel()
                return
            prepared = completion.result
            if completion.outcome is EffectOutcome.SUCCEEDED and isinstance(
                prepared, PreparedPreview
            ):
                show_preview(request.ports(), prepared.data, prepared.audio)
            else:
                request.toast(request.fallback, "warn")
        finally:
            self._pump()
