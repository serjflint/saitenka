"""The impure ends of the hovered-word commands: speak, copy, kanji, pause."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app import hover_intents
from saitenka.app.intents import Announce
from saitenka.app.media import speak
from saitenka.app.subtitles import box_for_token
from saitenka.model import is_ideograph
from saitenka.runtime import events

if TYPE_CHECKING:
    from collections.abc import Sequence

    from saitenka.app.dictionary import DictionarySet
    from saitenka.app.reader_context import InteractionContext
    from saitenka.app.subtitles import WordBox
    from saitenka.app.tokenize import Token
    from saitenka.runtime.interaction_slice import HoveredWordStore


class HoverHost(Protocol):
    """This feature's whole host coupling. See `PanelHost` for why it is spelled out.

    `hover` and `pause_on_tooltip` are host state this feature owns in everything but location; the
    port narrows when they move to a slice of their own.
    """

    hover: int
    pause_on_tooltip: bool
    interaction: InteractionContext

    @property
    def tokens(self) -> Sequence[Token]: ...

    @property
    def boxes(self) -> list[WordBox]: ...

    @property
    def sub_origin(self) -> tuple[int, int]: ...

    @property
    def dict_set(self) -> DictionarySet | None: ...

    @property
    def word_store(self) -> HoveredWordStore: ...

    def copy_token(self, t: Token, /) -> None: ...

    def open_kanji(self, ch: str, wx: float, wy: float, wh: float) -> None: ...

    def resume_after_hover_pause(self) -> None: ...

    def toast(self, text: str, kind: str = ..., seconds: float = ...) -> None: ...


class HoverAdapter:
    def __init__(self, host: HoverHost) -> None:
        self._host = host

    def inputs(self) -> hover_intents.HoverInputs:
        host = self._host
        # The pause policy is read whether or not a word is hovered: toggling it is what a user
        # does before hovering anything, so an early return on "nothing hovered" would silently
        # hand the reducer a default of False and flip the setting the wrong way.
        if not 0 <= host.hover < len(host.tokens):
            return hover_intents.HoverInputs(
                pause_on_tooltip=host.pause_on_tooltip,
                paused_by_tooltip=host.interaction.hover_pause.held,
            )
        token = host.tokens[host.hover]
        return hover_intents.HoverInputs(
            hovered=True,
            surface=token.surface,
            reading=host.word_store.current.reading,
            token_reading=token.reading,
            kanji=tuple(char for char in token.surface if is_ideograph(char)),
            kanji_index=host.word_store.current.kanji,
            has_dictionaries=host.dict_set is not None,
            anchored=box_for_token(host.boxes, host.hover) is not None,
            pause_on_tooltip=host.pause_on_tooltip,
            paused_by_tooltip=host.interaction.hover_pause.held,
        )

    def apply(self, effect: object, /) -> None:
        host = self._host
        if isinstance(effect, hover_intents.SpeakText):
            speak(effect.text)
        elif isinstance(effect, hover_intents.CopyToken):
            host.copy_token(host.tokens[host.hover])
        elif isinstance(effect, hover_intents.OpenKanji):
            box = box_for_token(host.boxes, host.hover)
            assert box is not None  # the reducer only opens against an anchored token
            origin_x, origin_y = host.sub_origin
            host.word_store.dispatch(events.HoverKanjiAdvanced())
            host.open_kanji(effect.char, origin_x + box.x, origin_y + box.y, box.h)
        elif isinstance(effect, hover_intents.SetHoverPause):
            host.pause_on_tooltip = effect.enabled
        elif isinstance(effect, hover_intents.ResumePlayback):
            host.resume_after_hover_pause()
        elif isinstance(effect, Announce):
            host.toast(effect.text, effect.kind)
