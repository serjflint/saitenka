from __future__ import annotations

from dataclasses import dataclass, field

from util import FakeIPC

from saitenka.app.features.translation import TranslationController, TranslationInputs
from saitenka.app.overlay_ids import OverlayId
from saitenka.runtime.presentation_slice import TranslationStore


@dataclass
class Surfaces:
    presented: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)

    def present(self, _image, _x, _y, *, oid: int) -> None:
        self.presented.append(oid)

    def remove(self, oid: int) -> None:
        self.removed.append(oid)


@dataclass
class Lease:
    held: bool = False

    def acquire(self) -> None:
        self.held = True

    def release(self) -> None:
        self.held = False


def inputs(*, selected: bool = False, text: object = "English") -> TranslationInputs:
    return TranslationInputs(
        surfaces_visible=True,
        tooltip_selected=selected,
        secondary_text=text,
        osd=(1920, 1080),
    )


def owner(*, auto: bool = False) -> tuple[TranslationController, Surfaces, Lease]:
    surfaces = Surfaces()
    lease = Lease()
    controller = TranslationController(
        TranslationStore(FakeIPC()), surfaces, lease, auto_reveal=auto
    )
    return controller, surfaces, lease


def test_manual_toggle_owns_the_hold_lease_and_drawn_text() -> None:
    controller, surfaces, lease = owner()

    controller.toggle(inputs())

    assert controller.state.held
    assert controller.state.drawn == "English"
    assert lease.held
    assert surfaces.presented == [OverlayId.TRANS]


def test_auto_reveal_ending_does_not_release_a_manual_hold() -> None:
    controller, _surfaces, lease = owner(auto=True)
    controller.toggle(inputs(selected=True))

    controller.sync_auto_reveal(lambda: inputs(selected=False))

    assert controller.state.held
    assert controller.state.drawn == "English"
    assert lease.held


def test_secondary_text_change_updates_only_an_active_reveal() -> None:
    controller, surfaces, _lease = owner(auto=True)

    controller.secondary_text_changed(inputs(selected=False, text="hidden"))
    controller.secondary_text_changed(inputs(selected=True, text="visible"))
    controller.secondary_text_changed(inputs(selected=True, text="visible"))

    assert controller.state.drawn == "visible"
    assert surfaces.presented == [OverlayId.TRANS]


def test_episode_retirement_clears_drawn_text_but_keeps_the_manual_hold() -> None:
    controller, surfaces, lease = owner()
    controller.toggle(inputs())

    controller.retire_episode()

    assert controller.state.held
    assert controller.state.drawn is None
    assert surfaces.removed == [OverlayId.TRANS]
    assert not lease.held
