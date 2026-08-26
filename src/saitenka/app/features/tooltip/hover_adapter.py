"""The impure ends of the hovered-word commands: speak, copy, kanji, pause."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.tooltip import hover_intents
from saitenka.app.intents import Announce
from saitenka.app.media import speak
from saitenka.app.subtitles import box_for_token
from saitenka.model import is_ideograph
from saitenka.runtime import events

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.session.context import InteractionContext
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.app.toast_controller import NotificationSink
    from saitenka.app.tokenize import Token


@dataclass(frozen=True, slots=True)
class HoverCommandPorts:
    """Authorities needed by hover commands, without retaining the session shell."""

    interaction: InteractionContext
    profile: ProfileController
    tooltip: TooltipController
    cue: CueRenderStore
    copy_token: Callable[[Token], None]
    open_kanji: Callable[[str, float, float, float], None]
    resume_playback: Callable[[], None]
    notifications: NotificationSink


class HoverCommandCoordinator:
    """Coordinate tooltip commands with playback, clipboard, TTS, and nested UI."""

    def __init__(self, ports: HoverCommandPorts) -> None:
        self._ports = ports

    def inputs(self) -> hover_intents.HoverInputs:
        ports = self._ports
        owner = ports.tooltip
        cue = ports.cue.current
        tokens = cue.tokens
        # The pause policy is read whether or not a word is hovered: toggling it is what a user
        # does before hovering anything, so an early return on "nothing hovered" would silently
        # hand the reducer a default of False and flip the setting the wrong way.
        if not 0 <= owner.selected < len(tokens):
            return hover_intents.HoverInputs(
                pause_on_tooltip=owner.pause_enabled,
                paused_by_tooltip=ports.interaction.hover_pause.held,
            )
        token = tokens[owner.selected]
        return hover_intents.HoverInputs(
            hovered=True,
            surface=token.surface,
            reading=owner.word_store.current.reading,
            token_reading=token.reading,
            kanji=tuple(char for char in token.surface if is_ideograph(char)),
            kanji_index=owner.word_store.current.kanji,
            has_dictionaries=ports.profile.dict_set is not None,
            anchored=box_for_token(cue.boxes, owner.selected) is not None,
            pause_on_tooltip=owner.pause_enabled,
            paused_by_tooltip=ports.interaction.hover_pause.held,
        )

    def apply(self, effect: hover_intents.HoverEffect, /) -> None:
        ports = self._ports
        owner = ports.tooltip
        cue = ports.cue.current
        tokens = cue.tokens
        if isinstance(effect, hover_intents.SpeakText):
            speak(effect.text)
        elif isinstance(effect, hover_intents.CopyToken):
            ports.copy_token(tokens[owner.selected])
        elif isinstance(effect, hover_intents.OpenKanji):
            box = box_for_token(cue.boxes, owner.selected)
            assert box is not None  # the reducer only opens against an anchored token
            origin_x, origin_y = cue.origin
            owner.word_store.dispatch(events.HoverKanjiAdvanced())
            ports.open_kanji(effect.char, origin_x + box.x, origin_y + box.y, box.h)
        elif isinstance(effect, hover_intents.SetHoverPause):
            owner.set_pause_enabled(enabled=effect.enabled)
        elif isinstance(effect, hover_intents.ResumePlayback):
            ports.resume_playback()
        elif isinstance(effect, Announce):
            ports.notifications.show(effect.text, effect.kind)
