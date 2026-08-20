"""The mined-card preview: what it is showing, the clip its ▶ replays, and whether it is enlarged.

A fresh preview starts un-zoomed, and that is the only rule here — but it is a rule that was two
assignments at the show site, so a second way to show one would have had to remember the second
half. The zoom belongs *with* the content for the same reason: it describes that content, and a
dismiss that kept it would enlarge whatever came next.

Content and clip ride opaquely. `runtime` cannot name a composed panel or a media path, and nothing
here branches on either — it decides only whether there is something on screen.

What is deliberately not here: the drawn rectangle and its four button boxes (one paint's output),
and the clip's live `subprocess.Popen`. A reducer cannot kill a process, so the handle stays where
something can — the same wall a lifetime container is behind, one surface down.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class CardPreview:
    """The composed preview, the clip it can replay, and the enlarge toggle."""

    content: object | None = None
    audio: object | None = None
    zoom: bool = False

    @property
    def open(self) -> bool:
        """Shown iff something is composed — the uniform `SurfaceState` predicate the surface
        registry reads. It used to be "a rect is placed", which is the same answer one step later:
        a composed preview is always drawn before anything can look."""
        return self.content is not None


@dataclass(frozen=True, slots=True)
class PreviewTurn:
    state: CardPreview


def shown(content: object, audio: object) -> PreviewTurn:
    """Put a preview up. Un-zoomed, and taking no prior state for exactly that reason: a new card
    at the last card's magnification is a surprise, so nothing carries over."""
    return PreviewTurn(CardPreview(content=content, audio=audio))


def dismissed() -> PreviewTurn:
    """✕, Esc, or a new cue. The clip is forgotten with the panel — a ▶ on a preview that is gone
    has nothing to press, and keeping the path would let a replay resurrect a dismissed card."""
    return PreviewTurn(CardPreview())


def zoom_toggled(state: CardPreview) -> PreviewTurn:
    """Clicking the screenshot enlarges it to check the frame, and again to shrink back."""
    return PreviewTurn(replace(state, zoom=not state.zoom))
