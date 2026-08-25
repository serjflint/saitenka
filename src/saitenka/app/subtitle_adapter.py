"""The impure ends of the subtitle commands: track selection, acquisition, seek, delay, copy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from saitenka.app import subtitle_intents, subtitle_modes
from saitenka.app.intents import Announce
from saitenka.app.media import copy_clipboard
from saitenka.app.mpv_egress import send_correlated
from saitenka.runtime import events
from saitenka.runtime.effects import Owner

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from saitenka.app.languages import Language
    from saitenka.app.session.context import EpisodeContext
    from saitenka.app.subtitle_fetch import SubtitleFetchRequest
    from saitenka.app.tokenize import Token
    from saitenka.mpvio.ipc import MpvIPC
    from saitenka.runtime.presentation_slice import TranslationStore


class SubtitleHost(Protocol):
    """This feature's whole host coupling. See `PanelHost` for why it is spelled out.

    Declaring these members changes nothing about the coupling except that it can be counted and
    argued about. The port narrows when `annotation_mode` and `annotation_hover` move to a slice —
    not by hiding them behind a `SessionController` parameter.
    """

    annotation_mode: subtitle_intents.AnnotationMode
    annotation_hover: bool
    sub_text: str

    @property
    def ipc(self) -> MpvIPC: ...

    @property
    def episode(self) -> EpisodeContext: ...

    @property
    def lines(self) -> Sequence[Sequence[Token]]: ...

    @property
    def subtitle_slang(self) -> str: ...

    @property
    def subtitle_language(self) -> Language: ...

    @property
    def cue_revision(self) -> int: ...

    @property
    def track_ports(self) -> subtitle_modes.TrackPorts: ...

    @property
    def translation_store(self) -> TranslationStore: ...

    @property
    def translate_on(self) -> bool: ...

    def observed_property(self, name: str) -> object: ...

    def property_value(self, name: str) -> object | None: ...

    def text_property(self, prop: str) -> str | None: ...

    def submit_subtitle_fetch(
        self,
        request: SubtitleFetchRequest,
        *,
        name: str,
        on_done: Callable[[], None] | None = ...,
    ) -> None: ...

    def draw_subtitle(self) -> None: ...

    def seek_cue(self, effect: subtitle_intents.SeekCue) -> bool: ...

    def sentence_lines(self) -> list[str]: ...

    def reveal_translation(self) -> None: ...

    def hide_translation(self, *, release: bool) -> None: ...

    def translation_visible(self) -> bool: ...

    def toast(self, text: str, kind: str = ..., seconds: float = ...) -> None: ...


class SubtitleAdapter:
    def __init__(self, host: SubtitleHost) -> None:
        self._host = host

    def inputs(self) -> subtitle_intents.SubtitleInputs:
        from saitenka.app.subtitle_modes import _current_external_sub

        host = self._host
        index = host.episode.sub_index
        playhead = host.observed_property("time-pos")
        return subtitle_intents.SubtitleInputs(
            tracks=subtitle_modes.discover_tracks(host.ipc, host.subtitle_slang),
            active_sid=host.property_value("sid"),
            language=host.subtitle_language,
            annotation_mode=host.annotation_mode,
            has_cue=bool(host.sub_text.strip()),
            retry_in_flight=host.episode.subtitle.retry_active,
            media_path=host.text_property("path"),
            has_external_sub=_current_external_sub(host.ipc) is not None,
            has_cue_lines=bool(host.lines),
            cue_starts=tuple(cue.start for cue in index.cues) if index is not None else (),
            playhead=None if playhead is None else float(playhead),  # type: ignore[arg-type]
            sub_delay=float(host.observed_property("sub-delay") or 0.0),  # type: ignore[arg-type]
            cue_revision=host.cue_revision,
        )

    def apply(self, effect: subtitle_intents.SubtitleEffect, /) -> None:
        host = self._host
        if isinstance(effect, subtitle_intents.SelectTrack):
            subtitle_modes.select_track(host.track_ports, effect.sid, effect.target)
        elif isinstance(effect, subtitle_intents.AdoptCurrentAsTarget):
            subtitle_modes.adopt_current_as_target(host.track_ports, effect.sid)
        elif isinstance(effect, subtitle_intents.AcquireSubtitles):
            self._acquire(effect)
        elif isinstance(effect, subtitle_intents.SetAnnotationMode):
            host.annotation_mode = effect.mode
            host.annotation_hover = False
            if effect.redraw:
                host.draw_subtitle()
        elif isinstance(effect, subtitle_intents.SeekCue):
            host.seek_cue(effect)
        elif isinstance(effect, subtitle_intents.SetSubtitleDelay):
            send_correlated(
                host.ipc,
                "sub-delay",
                "set_property",
                "sub-delay",
                f"{effect.seconds:.3f}",
                owner=Owner.SUBTITLE,
            )
        elif isinstance(effect, subtitle_intents.CopyCueText):
            copy_clipboard("\n".join(host.sentence_lines()))
        elif isinstance(effect, subtitle_intents.ToggleTranslation):
            self._toggle_translation()
        elif isinstance(effect, Announce):
            host.toast(effect.text, effect.kind)

    def _acquire(self, effect: subtitle_intents.AcquireSubtitles) -> None:
        host = self._host
        subtitle_modes.begin_acquisition(
            host.submit_subtitle_fetch,
            host.property_value,
            host.toast,
            lambda: host.episode.subtitle,
            host.ipc,
            effect.media_path,
            effect.source,
        )

    def _toggle_translation(self) -> None:
        host = self._host
        host.translation_store.dispatch(events.TranslationHeld(not host.translate_on))
        if host.translation_visible():
            host.reveal_translation()
        else:
            host.hide_translation(release=True)
