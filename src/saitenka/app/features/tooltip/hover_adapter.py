"""The impure ends of the hovered-word commands: speak, copy, kanji, pause."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from saitenka.app.features.tooltip import hover_intents
from saitenka.app.intents import Announce
from saitenka.app.media import speak
from saitenka.app.subtitles import box_for_token
from saitenka.model import is_ideograph

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka.app.features.profiles.profile_controller import ProfileController
    from saitenka.app.features.tooltip.tooltip_controller import TooltipController
    from saitenka.app.subtitle_presentation import CueRenderStore
    from saitenka.app.toast_controller import NotificationSink
    from saitenka.app.tokenize import Token


@dataclass(frozen=True, slots=True)
class HoverCommandPorts:
    """Authorities needed by hover commands, without retaining the session shell."""

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
        observed = owner.observation()
        cue = ports.cue.current
        tokens = cue.tokens
        # The pause policy is read whether or not a word is hovered: toggling it is what a user
        # does before hovering anything, so an early return on "nothing hovered" would silently
        # hand the reducer a default of False and flip the setting the wrong way.
        if not 0 <= observed.selected < len(tokens):
            return hover_intents.HoverInputs(
                pause_on_tooltip=observed.pause_enabled,
                paused_by_tooltip=observed.pause.held,
            )
        token = tokens[observed.selected]
        return hover_intents.HoverInputs(
            hovered=True,
            surface=token.surface,
            reading=observed.reading,
            token_reading=token.reading,
            kanji=tuple(char for char in token.surface if is_ideograph(char)),
            kanji_index=observed.kanji_index,
            has_dictionaries=ports.profile.dict_set is not None,
            anchored=box_for_token(cue.boxes, observed.selected) is not None,
            pause_on_tooltip=observed.pause_enabled,
            paused_by_tooltip=observed.pause.held,
        )

    def apply(self, effect: hover_intents.HoverEffect, /) -> None:
        ports = self._ports
        owner = ports.tooltip
        observed = owner.observation()
        cue = ports.cue.current
        tokens = cue.tokens
        if isinstance(effect, hover_intents.SpeakText):
            speak(effect.text)
        elif isinstance(effect, hover_intents.CopyToken):
            ports.copy_token(tokens[observed.selected])
        elif isinstance(effect, hover_intents.OpenKanji):
            box = box_for_token(cue.boxes, observed.selected)
            assert box is not None  # the reducer only opens against an anchored token
            origin_x, origin_y = cue.origin
            owner.advance_kanji()
            ports.open_kanji(effect.char, origin_x + box.x, origin_y + box.y, box.h)
        elif isinstance(effect, hover_intents.SetHoverPause):
            owner.set_pause_enabled(enabled=effect.enabled)
        elif isinstance(effect, hover_intents.ResumePlayback):
            ports.resume_playback()
        elif isinstance(effect, Announce):
            ports.notifications.show(effect.text, effect.kind)
