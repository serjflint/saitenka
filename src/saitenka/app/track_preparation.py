"""Prepare track artifacts on a worker; adopt only the selection that requested them."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from saitenka_subtitles import MAX_ASS_SOURCE_BYTES, font_names, subrip

from saitenka.app import embedded_subs, subtitle_artifact, subtitle_fonts
from saitenka.app.sub_index import load_index
from saitenka.runtime import EffectOutcome, Owner
from saitenka.runtime.jobs import JobLanePolicy, configure_lane

if TYPE_CHECKING:
    from collections.abc import Callable

    from saitenka_subtitles import CueIndex

    from saitenka.mpvio.ipc import MpvIPC

LANE = "track-preparation"
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedSource:
    raw: bytes
    families: frozenset[str]
    markup: dict


def prepare_source(path: Path) -> PreparedSource:
    with path.open("rb") as stream:
        raw = stream.read(MAX_ASS_SOURCE_BYTES + 1)
    if len(raw) > MAX_ASS_SOURCE_BYTES:
        return PreparedSource(raw, frozenset(), {})
    families = font_names.in_document(raw) if path.suffix.casefold() == ".ass" else frozenset()
    try:
        markup = (
            subrip.markup_by_cue(raw.decode("utf-8"))
            if not families and path.suffix.casefold() != ".ass"
            else {}
        )
    except UnicodeDecodeError:
        markup = {}
    return PreparedSource(raw, families, markup)


@dataclass(frozen=True, slots=True)
class TrackRequest:
    ipc: MpvIPC
    properties: dict
    epoch: int
    cancelled: threading.Event


@dataclass(frozen=True, slots=True)
class PreparedTrack:
    path: Path
    media: object
    sid: object
    codec: str
    index: CueIndex
    source: PreparedSource
    fonts: subtitle_fonts.FontEnvironment
    track: dict


def _read(request: TrackRequest, cancelled: Callable[[], bool], command: str, name: str):
    if cancelled():
        raise OSError("track preparation cancelled")
    if command == "get_property" and name in request.properties:
        return request.properties[name]
    pending = request.ipc.command_async(
        command, name, expected_connection_epoch=request.epoch
    ).future
    deadline = time.monotonic() + 10
    while not cancelled():
        try:
            reply = pending.result(timeout=0.1)
            if reply.get("error") in {"disconnected", "stale-epoch"}:
                break
            return reply.get("data") if reply.get("error") in {None, "success"} else None
        except FutureTimeoutError:
            if time.monotonic() >= deadline:
                break
    raise OSError("track preparation read failed or cancelled")


def _expand(request, cancelled, name) -> str | None:
    value = _read(request, cancelled, "expand-path", name)
    return value if isinstance(value, str) else None


def _prepare_fonts(request, get, cancelled, media):
    if not request.properties.get("_geometry", True):
        return subtitle_fonts.FontEnvironment()
    try:
        settings = {name: get(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS}
        return subtitle_fonts.resolve(
            expand=partial(_expand, request, cancelled),
            settings=settings,
            video=Path(str(media)) if media else None,
            cache_dir=embedded_subs.container_fonts_cache_dir(),
            cancelled=cancelled,
        )
    except (OSError, ValueError, TypeError) as error:
        log.warning("could not resolve the track's font sources: %s", error)
        return subtitle_fonts.FontEnvironment()


def _selected_track(properties: dict) -> dict | None:
    raw = properties.get("track-list")
    tracks = [
        t
        for t in (raw if isinstance(raw, list) else [])
        if isinstance(t, dict) and t.get("type") == "sub"
    ]
    if "sid" in properties:
        return next((t for t in tracks if t.get("id") == properties["sid"]), None)
    return min(
        (t for t in tracks if t.get("selected")),
        key=lambda t: t.get("main-selection", 0),
        default=None,
    )


def prepare_track(request: object, cancelled: threading.Event) -> PreparedTrack | None:
    if not isinstance(request, TrackRequest):
        raise TypeError("invalid track preparation request")
    if cancelled.is_set() or request.cancelled.is_set():
        return None

    def should_cancel() -> bool:
        return request.cancelled.is_set() or cancelled.is_set()

    get = partial(_read, request, should_cancel, "get_property")
    raw_tracks = get("track-list")
    selected = _selected_track({**request.properties, "track-list": raw_tracks or []})
    if selected is None:
        return None
    media = get("path")
    path = _artifact_path(subtitle_artifact.resolve(selected, media_path=media), should_cancel)
    if path is None:
        return None
    fonts = _prepare_fonts(request, get, should_cancel, media)
    if should_cancel():
        return None
    source = prepare_source(path)
    if len(source.raw) > MAX_ASS_SOURCE_BYTES:
        return None
    index = load_index(path, content=source.raw.decode("utf-8", errors="replace"))
    if index is None or cancelled.is_set() or request.cancelled.is_set():
        return None
    return PreparedTrack(
        path,
        media,
        selected.get("id"),
        str(selected.get("codec") or ""),
        index,
        source,
        fonts,
        dict(selected),
    )


def _artifact_path(artifact, cancelled) -> Path | None:
    if isinstance(artifact, subtitle_artifact.ArtifactUnavailable):
        return None
    if isinstance(artifact, subtitle_artifact.ExternalArtifact):
        return Path(artifact.path)
    suffix, codec_args = subtitle_artifact.extract_spec(artifact.codec)
    path = embedded_subs.embedded_subs_cache_dir() / embedded_subs.embedded_subs_cache_key(
        artifact.media_path, artifact.ff_index, suffix
    )
    if path.exists() or embedded_subs.extract_embedded_track(
        artifact.media_path, artifact.ff_index, path, codec_args, cancelled=cancelled
    ):
        return path
    return None


class TrackPreparation:
    def __init__(
        self,
        ipc,
        current: Callable[[], dict],
        adopt: Callable[[PreparedTrack], None],
        failed: Callable[[dict], None],
    ):
        self._ipc = ipc
        self._current = current
        self._adopt = adopt
        self._failed = failed
        self._pending: TrackRequest | None = None
        self._active: TrackRequest | None = None
        self._submit = configure_lane(ipc, LANE, JobLanePolicy(capacity=1), prepare_track)

    def request(self) -> None:
        self.cancel()
        properties = self._current()
        self._pending = TrackRequest(
            self._ipc, properties, properties.get("_epoch", 0), threading.Event()
        )
        self._pump()

    def _pump(self) -> None:
        if self._active is not None or self._pending is None or self._submit is None:
            return
        request, self._pending = self._pending, None
        self._active = request

        def finished(completion) -> None:
            self._active = None
            try:
                self._finish(request, completion)
            finally:
                self._pump()

        accepted = self._submit(
            owner=Owner.SUBTITLE,
            identity=id(request),
            lane=LANE,
            request=request,
            on_finished=finished,
        )
        if not accepted:
            self._active = None

    def _finish(self, request, completion) -> None:
        if request.cancelled.is_set():
            return
        now = self._current()
        if any(
            now.get(name) != request.properties.get(name)
            for name in ("_epoch", "path", "sid", "track-list")
        ):
            return
        result = completion.result
        if completion.outcome is not EffectOutcome.SUCCEEDED or not isinstance(
            result, PreparedTrack
        ):
            self._failed(request.properties)
            return
        if now.get("path") != result.media or now.get("sid") != result.sid:
            return
        settings = {name: now.get(f"options/{name}") for name in subtitle_fonts.FONT_OPTIONS}
        if (
            request.properties.get("_geometry", True)
            and result.fonts.options
            and subtitle_fonts.option_snapshot(settings) != result.fonts.options
        ):
            return
        self._adopt(result)

    def cancel(self) -> None:
        if self._active is not None:
            self._active.cancelled.set()
        self._pending = None
