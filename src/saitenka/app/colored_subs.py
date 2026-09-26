"""Colored copies of authored subtitle tracks on disk, and the track-list view that hides their origin.

A copy stands in for the authored track it was made from: while one is loaded, the origin is left out
of discovery, so the Japanese role, Alt+t and the translation lease all land on the copy. The copy is
identified by where it lives, not by its title, and names its origin in its own header.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka_subtitles import MAX_ASS_SOURCE_BYTES, UnsupportedAssEvent
from saitenka_subtitles.colored_track import (
    HEAD_BYTES,
    INJECTED,
    color_document,
    colored_origin,
    strip_colors,
)

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable, Mapping

    from saitenka_subtitles.document import TokenAnnotation
    from saitenka_tokenize.japanese import Token

    from saitenka.app.token_cache import TokenizedCue

log = logging.getLogger(__name__)

REFUSING_OPTIONS = ("sub-ass-override", "sub-filter-sdh", "sub-ass-style-overrides")
_COLOR_STYLE_KEYS = ("primarycolour", "secondarycolour", "outlinecolour", "backcolour")


def colored_subs_dir() -> Path:
    from saitenka.app.paths import cache_dir

    return cache_dir() / "colored-subs"


def _external(track: Mapping[str, object]) -> str | None:
    path = track.get("external-filename")
    return str(path) if track.get("external") and path else None


def is_copy(track: Mapping[str, object]) -> bool:
    path = _external(track)
    return path is not None and Path(path).parent == colored_subs_dir()


@lru_cache(maxsize=32)
def _recorded_origin(path: str) -> str | None:
    # Copies are content-addressed, so a path never changes what it holds.
    try:
        with Path(path).open("rb") as copy:
            head = copy.read(HEAD_BYTES)
    except OSError:
        return None
    return colored_origin(head.decode("utf-8", errors="replace"))


def copy_origin(track: Mapping[str, object]) -> str | None:
    """The origin a loaded copy names, or `None` for a track that is not one."""
    path = _external(track)
    return _recorded_origin(path) if path is not None and is_copy(track) else None


def origin_ref(track: Mapping[str, object]) -> str | None:
    """How a copy names `track` as its origin, within the file both are loaded for."""
    path = _external(track)
    if path is not None:
        return f"external:{path}"
    ff_index = track.get("ff-index")
    return None if ff_index is None or track.get("external") else f"embedded:{ff_index}"


def hide_origins(tracks: list[dict]) -> list[dict]:
    """`tracks` without the authored tracks whose copies are loaded beside them."""
    hidden = {origin for track in tracks if (origin := copy_origin(track)) is not None}
    if not hidden:
        return tracks
    return [track for track in tracks if origin_ref(track) not in hidden]


def origin_track(tracks: list[dict], track: Mapping[str, object]) -> dict | None:
    """The authored track `track` is a copy of, when it is one and its origin is still loaded."""
    origin = copy_origin(track)
    if origin is None:
        return None
    return next((candidate for candidate in tracks if origin_ref(candidate) == origin), None)


def refusing_options(options: Mapping[str, object]) -> str | None:
    """An mpv setting under which the copy's colors would not be the ones on screen."""
    if str(options.get("sub-ass-override")) in {"strip", "force"}:
        return "sub-ass-override"
    if options.get("sub-filter-sdh") is True:
        return "sub-filter-sdh"
    overrides = options.get("sub-ass-style-overrides") or ()
    if isinstance(overrides, str):
        overrides = overrides.split(",")
    for override in overrides if isinstance(overrides, list | tuple) else ():
        key = str(override).partition("=")[0].rpartition(".")[2].strip().casefold()
        if key in _COLOR_STYLE_KEYS:
            return "sub-ass-style-overrides"
    return None


def frame_colored(rows: object) -> bool:
    """Whether every active event of a copy's frame was colored — the track paints all of it."""
    if not isinstance(rows, str):
        return False
    events = [row for row in rows.splitlines() if row]
    return bool(events) and all(INJECTED.search(row) for row in events)


def track_paints(value: Callable[[str], object]) -> bool:
    """Whether mpv is drawing the current frame's colors itself, from a colored copy.

    The selected track has to be a copy: an authored track can carry the injected form itself. The
    copy is recognized by its header, so an attached session recognizes one it did not load.
    """
    if not frame_colored(value("sub-text/ass-full")):
        return False
    tracks = value("track-list")
    sid = value("sid")
    selected = next(
        (
            track
            for track in (tracks if isinstance(tracks, list) else ())
            if isinstance(track, dict) and track.get("type") == "sub" and track.get("id") == sid
        ),
        None,
    )
    options = {name: value(f"options/{name}") for name in REFUSING_OPTIONS}
    return (
        selected is not None
        and copy_origin(selected) is not None
        and refusing_options(options) is None
    )


def event_colors(
    tokenize: Callable[[str], TokenizedCue],
    is_skippable: Callable[[Token], bool],
) -> Callable[[str], tuple[tuple[TokenAnnotation, ...], dict[int, int]] | None]:
    """The generator's scorer: the tokens live annotation would color in one event, and their RGB."""

    from saitenka.app.native_subtitles import token_annotations

    def colors(text: str) -> tuple[tuple[TokenAnnotation, ...], dict[int, int]] | None:
        cue = tokenize(text)
        if not cue.tokens or cue.styles is None:
            return None
        selection = token_annotations(text, cue.lines, cue.tokens, is_skippable)
        rgb = {}
        for token in selection.annotations:
            red, green, blue = cue.styles[token.token_index].color[:3]
            rgb[token.token_index] = (red << 16) | (green << 8) | blue
        return selection.annotations, rgb

    return colors


def _digest(value: str) -> str:
    return hashlib.blake2s(value.encode("utf-8"), digest_size=8).hexdigest()


def write_copy(origin_key: str, text: str) -> Path:
    """Publish `text` under a content-addressed name. Older copies stay until the swap retires them."""
    directory = colored_subs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    prefix = _digest(origin_key)
    path = directory / f"{prefix}-{_digest(text)}.ass"
    if not path.exists():
        descriptor, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(text.encode("utf-8"))
            Path(temporary).replace(path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise
    return path


def evict_siblings(path: Path) -> None:
    """Delete the other copies of `path`'s origin, once mpv no longer has one selected."""
    prefix = path.name.partition("-")[0]
    for sibling in path.parent.glob(f"{prefix}-*.ass"):
        if sibling != path:
            sibling.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class ColorRequest:
    """Color the authored document at `source` — or, for a copy, the document it strips back to."""

    source: Path
    origin: str
    origin_key: str
    colors: Callable[[str], tuple[tuple[TokenAnnotation, ...], dict[int, int]] | None]


@dataclass(frozen=True, slots=True)
class ColorResult:
    path: Path | None
    reason: str
    colored: int = 0
    verbatim: tuple[tuple[str, int], ...] = ()


class Superseded(Exception):  # a control-flow signal, not an error
    pass


def _read_origin(source: Path) -> str:
    with source.open("rb") as handle:
        raw = handle.read(MAX_ASS_SOURCE_BYTES + 1)
    if len(raw) > MAX_ASS_SOURCE_BYTES:
        raise UnsupportedAssEvent("subtitle-source-too-large")
    text = raw.decode("utf-8")
    return strip_colors(text) if colored_origin(text) is not None else text


def generate(request: object, cancelled: threading.Event) -> object:
    """Job-lane handler: read, color, publish. Every refusal is a named result, never a raise."""
    if not isinstance(request, ColorRequest):
        raise TypeError("invalid colored-track request")

    def colors(text: str):
        if cancelled.is_set():
            raise Superseded
        return request.colors(text)

    try:
        document = color_document(_read_origin(request.source), request.origin, colors)
    except Superseded:
        return ColorResult(None, "superseded")
    except (OSError, UnicodeDecodeError):
        log.debug("colored track: origin unreadable", exc_info=True)
        return ColorResult(None, "origin-unreadable")
    except UnsupportedAssEvent as error:
        log.info("colored track refused: %s", error)
        return ColorResult(None, "document-refused")
    verbatim = tuple(sorted((reason.value, count) for reason, count in document.verbatim.items()))
    if not document.colored:
        return ColorResult(None, "nothing-colored", 0, verbatim)
    try:
        path = write_copy(request.origin_key, document.text)
    except OSError:
        log.warning("colored track: cannot write the copy", exc_info=True)
        return ColorResult(None, "write-failed", document.colored, verbatim)
    return ColorResult(path, "ready", document.colored, verbatim)
