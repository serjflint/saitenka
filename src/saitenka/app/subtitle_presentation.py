"""Owner-thread state derived while presenting the current subtitle cue."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from saitenka.app import subtitle_intents
    from saitenka.app.scoring import TokenStyle
    from saitenka.app.subtitles import WordBox
    from saitenka.app.token_cache import TokenizedCue
    from saitenka.app.tokenize import Token


@dataclass(frozen=True, slots=True)
class CueRenderState:
    """Tokenization and geometry derived from the cue owned by playback."""

    lines: list[list[Token]] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    styles: list[TokenStyle] | None = None
    boxes: list[WordBox] = field(default_factory=list)
    origin: tuple[int, int] = (0, 0)


class CueRenderStore:
    """Single writer for the current cue's derived render facts."""

    def __init__(self) -> None:
        self._current = CueRenderState()

    @property
    def current(self) -> CueRenderState:
        return self._current

    def reset(self) -> None:
        self._current = CueRenderState()

    def clear_annotation(self) -> None:
        state = self._current
        self._current = CueRenderState(boxes=state.boxes, origin=state.origin)

    def install_tokenized(self, cue: TokenizedCue) -> None:
        state = self._current
        self._current = CueRenderState(
            lines=cue.lines,
            tokens=cue.tokens,
            styles=cue.styles,
            boxes=state.boxes,
            origin=state.origin,
        )

    def clear_geometry(self) -> None:
        state = self._current
        self._current = CueRenderState(state.lines, state.tokens, state.styles)

    def publish_geometry(self, boxes: list[WordBox], origin: tuple[int, int]) -> None:
        state = self._current
        self._current = CueRenderState(state.lines, state.tokens, state.styles, boxes, origin)


@dataclass(frozen=True, slots=True)
class AnnotationState:
    mode: subtitle_intents.AnnotationMode
    hover_revealed: bool = False


class AnnotationController:
    """Session annotation preference plus the cue-local hover reveal."""

    def __init__(self, mode: subtitle_intents.AnnotationMode) -> None:
        self._state = AnnotationState(mode)

    @property
    def state(self) -> AnnotationState:
        return self._state

    def set_mode(self, mode: subtitle_intents.AnnotationMode) -> None:
        self._state = AnnotationState(mode)

    def set_hover_revealed(self, *, revealed: bool) -> None:
        self._state = AnnotationState(self._state.mode, revealed)

    def retire_cue(self) -> None:
        if self._state.hover_revealed:
            self._state = AnnotationState(self._state.mode)
