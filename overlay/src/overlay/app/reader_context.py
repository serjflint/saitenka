"""Reader state grouped by lifetime — the composition that shrinks the ``controller`` god-object (#30).

An mpv session outlives the file it plays; a hover outlives nothing. Grouping Reader state by *when it
is born and dies* — session ⊃ episode ⊃ interaction — is what makes a re-slot (swap the episode on a
file change, #100) correct by construction: rebind the context and no prior-episode state can leak. This
module owns the **episode** tier plus the ``Delegated`` descriptor the Reader uses to expose a context's
fields under their historical ``reader.<field>`` names while call sites migrate onto ``reader.episode.<field>``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from overlay.app.sub_index import SubIndex
    from overlay.app.subtitle_modes import Language


class Delegated[T]:
    """A typed attribute that reads/writes ``obj.<context>.<field>`` — the stable seam that lets the
    Reader own its state as lifetime contexts without breaking the ``reader.<field>`` call sites."""

    __slots__ = ("_context", "_field")

    def __init__(self, context: str, field: str) -> None:
        self._context = context
        self._field = field

    @overload
    def __get__(self, obj: None, _objtype: type | None = None) -> Delegated[T]: ...
    @overload
    def __get__(self, obj: object, _objtype: type | None = None) -> T: ...
    def __get__(self, obj: object | None, _objtype: type | None = None) -> Delegated[T] | T:
        if obj is None:
            return self  # class-level access (e.g. introspection) yields the descriptor
        return getattr(getattr(obj, self._context), self._field)

    def __set__(self, obj: object, value: T) -> None:
        setattr(getattr(obj, self._context), self._field, value)


class EpisodeContext:
    """State scoped to one played file: which subtitle tracks, and where we are in them. Rebuilt on
    every file change (#100 re-slot) — nothing here may outlive the episode."""

    def __init__(self) -> None:
        self.jp_sid: int | None = None
        self.en_sid: int | None = None
        self.subtitle_language: Language = "jp"
        self.subtitle_slang = "ja,jpn,jp"
        # external sub-index of the JP cue file → Alt+←/→/↓ render the target line INSTANTLY, decoupled
        # from mpv's slow video seek; the real sub-seek fires behind it and reconciles once it settles.
        self.sub_index: SubIndex | None = None
        self.nav_idx = -1  # last cue index jumped to (chaining hint; -1 = unknown)
        self.sub_settle_until = 0.0  # while >now, ignore transient-empty sub-text during a seek
        self.nav_prev_text = ""  # cue text showing right before a nav render (reconcile)
