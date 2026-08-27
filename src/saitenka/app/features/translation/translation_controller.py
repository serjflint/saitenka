"""Owner of translation reveal policy and presentation state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from saitenka.app import translation
from saitenka.app.overlay_ids import OverlayId
from saitenka.runtime import events

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL import Image

    from saitenka.runtime.presentation import TranslationState
    from saitenka.runtime.presentation_slice import TranslationStore


class SecondaryLease(Protocol):
    def acquire(self) -> object: ...

    def release(self) -> None: ...


class TranslationSurface(Protocol):
    def present(self, img: Image.Image, x: int, y: int, *, oid: int) -> object: ...

    def remove(self, oid: int) -> object: ...


@dataclass(frozen=True, slots=True)
class TranslationInputs:
    """Volatile facts sampled together for one reveal decision."""

    surfaces_visible: bool
    tooltip_selected: bool
    secondary_text: object
    osd: tuple[int, int]


class TranslationController:
    """One writer for reveal intent, drawn text, and secondary-track demand."""

    def __init__(
        self,
        store: TranslationStore,
        surfaces: TranslationSurface,
        lease: SecondaryLease,
        *,
        auto_reveal: bool,
    ) -> None:
        self._store = store
        self._surfaces = surfaces
        self._lease = lease
        self._auto_reveal = auto_reveal

    @property
    def state(self) -> TranslationState:
        return self._store.current

    def wanted(self, inputs: TranslationInputs) -> bool:
        return self.state.held or (self._auto_reveal and inputs.tooltip_selected)

    def active(self, inputs: TranslationInputs) -> bool:
        return inputs.surfaces_visible and self.wanted(inputs)

    def toggle(self, inputs: TranslationInputs) -> None:
        self._store.dispatch(events.TranslationHeld(not self.state.held))
        if self.active(inputs):
            self.reveal(inputs)
        else:
            self.hide(release=True)

    def sync_auto_reveal(self, inputs: Callable[[], TranslationInputs]) -> None:
        if not self._auto_reveal:
            return
        observed = inputs()
        if self.active(observed):
            self.reveal(observed)
        else:
            self.hide(release=not self.state.held)

    def reveal(self, inputs: TranslationInputs) -> None:
        self._lease.acquire()
        self.draw(inputs)

    def draw(self, inputs: TranslationInputs) -> None:
        text = translation.clean_secondary(inputs.secondary_text)
        self._store.dispatch(events.TranslationDrawn(text))
        if not text:
            self._surfaces.remove(OverlayId.TRANS)
            return
        image, x, y = translation.render_translation(text, inputs.osd)
        self._surfaces.present(image, x, y, oid=OverlayId.TRANS)

    def hide(self, *, release: bool) -> None:
        self._surfaces.remove(OverlayId.TRANS)
        self._store.dispatch(events.TranslationDrawn(None))
        if release:
            self._lease.release()

    def secondary_text_changed(self, inputs: TranslationInputs) -> None:
        text = translation.clean_secondary(inputs.secondary_text)
        if self.active(inputs) and text != self.state.drawn:
            self.draw(inputs)

    def retire_episode(self) -> None:
        self.hide(release=True)
