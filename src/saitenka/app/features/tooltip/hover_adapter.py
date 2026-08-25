"""The impure ends of the hovered-word commands: speak, copy, kanji, pause."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app.features.tooltip import hover_intents
from saitenka.app.intents import Announce
from saitenka.app.media import speak
from saitenka.app.subtitles import box_for_token
from saitenka.model import is_ideograph
from saitenka.runtime import events

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.session.context import InteractionContext
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token


class HoverHost(Protocol):
    """This feature's whole host coupling. See `PanelHost` for why it is spelled out."""

    interaction: InteractionContext
    profile_controller: ProfileController
    tooltip_controller: TooltipController

    @property
    def tokens(self) -> Sequence[Token]: ...

    @property
    def boxes(self) -> list[WordBox]: ...

    @property
    def sub_origin(self) -> tuple[int, int]: ...

    def copy_token(self, t: Token, /) -> None: ...

    def open_kanji(self, ch: str, wx: float, wy: float, wh: float) -> None: ...

    def resume_after_hover_pause(self) -> None: ...

    def toast(self, text: str, kind: str = ..., seconds: float = ...) -> None: ...


class HoverAdapter:
    def __init__(self, host: HoverHost) -> None:
        self._host = host

    def inputs(self) -> hover_intents.HoverInputs:
        host = self._host
        owner = host.tooltip_controller
        # The pause policy is read whether or not a word is hovered: toggling it is what a user
        # does before hovering anything, so an early return on "nothing hovered" would silently
        # hand the reducer a default of False and flip the setting the wrong way.
        if not 0 <= owner.selected < len(host.tokens):
            return hover_intents.HoverInputs(
                pause_on_tooltip=owner.pause_enabled,
                paused_by_tooltip=host.interaction.hover_pause.held,
            )
        token = host.tokens[owner.selected]
        return hover_intents.HoverInputs(
            hovered=True,
            surface=token.surface,
            reading=owner.word_store.current.reading,
            token_reading=token.reading,
            kanji=tuple(char for char in token.surface if is_ideograph(char)),
            kanji_index=owner.word_store.current.kanji,
            has_dictionaries=host.profile_controller.dict_set is not None,
            anchored=box_for_token(host.boxes, owner.selected) is not None,
            pause_on_tooltip=owner.pause_enabled,
            paused_by_tooltip=host.interaction.hover_pause.held,
        )

    def apply(self, effect: hover_intents.HoverEffect, /) -> None:
        host = self._host
        owner = host.tooltip_controller
        if isinstance(effect, hover_intents.SpeakText):
            speak(effect.text)
        elif isinstance(effect, hover_intents.CopyToken):
            host.copy_token(host.tokens[owner.selected])
        elif isinstance(effect, hover_intents.OpenKanji):
            box = box_for_token(host.boxes, owner.selected)
            assert box is not None  # the reducer only opens against an anchored token
            origin_x, origin_y = host.sub_origin
            owner.word_store.dispatch(events.HoverKanjiAdvanced())
            host.open_kanji(effect.char, origin_x + box.x, origin_y + box.y, box.h)
        elif isinstance(effect, hover_intents.SetHoverPause):
            owner.set_pause_enabled(enabled=effect.enabled)
        elif isinstance(effect, hover_intents.ResumePlayback):
            host.resume_after_hover_pause()
        elif isinstance(effect, Announce):
            host.toast(effect.text, effect.kind)
